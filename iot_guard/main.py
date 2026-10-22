from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .classifier import classify
from .config import Rules, load_config, normalize_mac
from .oui import refresh
from .reconciler import make_comment, reconcile
from .routeros import RouterOS

log = logging.getLogger("iot_guard")
LIST_NAME = "iot-blocked"


def run_once(router, oui, rules: Rules, list_name: str, *, dry_run: bool, logged: set | None = None) -> None:
    logged = logged if logged is not None else set()
    desired: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for lease in router.leases():
        mac = normalize_mac(lease.mac)
        override = rules.overrides.get(mac)
        if not lease.active and not (override and override.action == "block"):
            continue  # offline static lease: only explicit blocks matter
        verdict = classify(lease, oui.vendor(mac), rules)
        key = (mac, lease.address, verdict.block, tuple(verdict.reasons))
        if key not in logged:
            logged.add(key)
            if verdict.unclassified:
                log.info("UNCLASSIFIED %s %s %r %r", mac, oui.vendor(mac), lease.host_name, lease.class_id)
            else:
                log.info("%s %s %s: %s", "BLOCK" if verdict.block else "allow", mac, lease.address, "; ".join(verdict.reasons))
        if verdict.block:
            desired[mac] = lease.address
            reasons[mac] = verdict.reasons[0]

    plan = reconcile(desired, router.blocked_entries(list_name))
    prefix = "[dry-run] " if dry_run else ""
    for a in plan.add:
        log.info("%sadd %s %s", prefix, a.address, a.mac)
    for e, ip in plan.update:
        log.info("%supdate %s -> %s", prefix, e.address, ip)
    for e in plan.remove:
        log.info("%sremove %s (%s)", prefix, e.address, e.comment)
    if dry_run:
        return

    for a in plan.add:
        router.add_blocked(list_name, a.address, make_comment(a.mac, reasons[a.mac]))
    for e, ip in plan.update:
        router.update_blocked_address(e.id, ip)
    for e in plan.remove:
        router.remove_blocked(e.id)
    for address in plan.new_block_addresses:
        n = router.drop_connections(address)
        log.info("dropped %d connection(s) from %s", n, address)


def _router_from_env() -> RouterOS:
    verify: bool | str = os.environ.get("ROUTEROS_CA_CERT") or True
    return RouterOS(
        os.environ["ROUTEROS_URL"],
        os.environ["ROUTEROS_USER"],
        os.environ["ROUTEROS_PASSWORD"],
        verify=verify,
    )


def cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="iot-guard")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "leases"])
    ap.add_argument("--config", default=os.environ.get("IOT_GUARD_CONFIG", "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="log planned changes without applying")
    ap.add_argument("--once", action="store_true", help="run a single iteration and exit")
    ap.add_argument("--interval", type=float, default=30)
    ap.add_argument("--oui-file", default=os.environ.get("IOT_GUARD_OUI", "/data/oui.csv"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    router = _router_from_env()
    if args.command == "leases":  # read-only smoke test
        oui = refresh(args.oui_file)
        for l in router.leases():
            print(l.mac, l.address, l.host_name or "-", l.class_id or "-", oui.vendor(l.mac) or "-", "" if l.active else "(offline)")
        return 0

    rules = load_config(args.config)
    oui = refresh(args.oui_file)
    for problem in router.ipv6_internet_problems():
        log.warning("IPv6 internet may be reachable, blocked devices could bypass the IPv4 rule: %s", problem)
    logged: set = set()
    last_oui = time.monotonic()
    while True:
        try:
            run_once(router, oui, rules, LIST_NAME, dry_run=args.dry_run, logged=logged)
        except Exception:
            log.exception("iteration failed; entries on the router are left as-is")
        if args.once:
            return 0
        if time.monotonic() - last_oui > 86400:
            oui, last_oui = refresh(args.oui_file), time.monotonic()
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(cli())
