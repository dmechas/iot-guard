from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import httpx

from .classifier import classify
from .config import ConfigWatcher, Rules, normalize_mac
from .oui import refresh
from .reconciler import make_comment, reconcile
from .routeros import RouterOS

log = logging.getLogger("iot_guard")
LIST_NAME = "iot-blocked"


@dataclass
class State:
    """Carried across iterations."""

    logged: set = field(default_factory=set)  # verdicts already logged
    pending_drops: set[str] = field(default_factory=set)  # newly blocked IPs whose connections still need dropping


def _try(what: str, fn, *args) -> bool:
    try:
        fn(*args)
        return True
    except Exception as e:
        log.error("%s failed: %s", what, e)
        return False


def run_once(router, oui, rules: Rules, list_name: str, *, dry_run: bool, state: State | None = None) -> None:
    state = state if state is not None else State()
    leases = router.leases()
    if not leases:
        # Never act on an empty lease table (e.g. DHCP server restarting): it would unblock everything.
        log.warning("router returned no DHCP leases; skipping this iteration")
        return

    desired: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for lease in leases:
        mac = normalize_mac(lease.mac)
        override = rules.overrides.get(mac)
        if not lease.active and not (override and override.action == "block"):
            continue  # offline static lease: only explicit blocks matter
        vendor = oui.vendor(mac)
        verdict = classify(lease, vendor, rules)
        key = (mac, lease.address, verdict.block, tuple(verdict.reasons))
        if key not in state.logged:
            state.logged.add(key)
            if verdict.unclassified:
                log.info("UNCLASSIFIED %s %s %r %r", mac, vendor, lease.host_name, lease.class_id)
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

    # Each operation is independent: one failure must not stop the rest. Failed ones are
    # retried naturally on the next iteration because the plan is recomputed.
    for a in plan.add:
        if _try(f"add {a.address}", router.add_blocked, list_name, a.address, make_comment(a.mac, reasons[a.mac])):
            state.pending_drops.add(a.address)
    for e, ip in plan.update:
        if _try(f"update {e.address} -> {ip}", router.update_blocked_address, e.id, ip):
            state.pending_drops.add(ip)
    for e in plan.remove:
        _try(f"remove {e.address}", router.remove_blocked, e.id)

    blocked_now = set(desired.values())
    for address in sorted(state.pending_drops):
        if address not in blocked_now:
            state.pending_drops.discard(address)  # unblocked meanwhile; nothing to drop
            continue
        try:
            n = router.drop_connections(address)
        except Exception as e:
            log.error("dropping connections from %s failed, will retry: %s", address, e)
            continue
        state.pending_drops.discard(address)
        log.info("dropped %d connection(s) from %s", n, address)


def _router_from_env() -> RouterOS:
    verify: bool | str = os.environ.get("ROUTEROS_CA_CERT") or True
    if password_file := os.environ.get("ROUTEROS_PASSWORD_FILE"):
        with open(password_file) as f:
            password = f.read().strip()
    else:
        password = os.environ["ROUTEROS_PASSWORD"]
    return RouterOS(os.environ["ROUTEROS_URL"], os.environ["ROUTEROS_USER"], password, verify=verify)


def _check_ipv6(router) -> bool:
    try:
        problems = router.ipv6_internet_problems()
    except Exception as e:
        log.error("IPv6 check failed, will retry: %s", e)
        return False
    for problem in problems:
        log.warning("IPv6 internet may be reachable, blocked devices could bypass the IPv4 rule: %s", problem)
    return True


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

    config = ConfigWatcher(args.config)
    oui = refresh(args.oui_file)
    state = State()
    ipv6_checked = False
    last_oui = time.monotonic()
    while True:
        ipv6_checked = ipv6_checked or _check_ipv6(router)
        try:
            run_once(router, oui, config.rules(), LIST_NAME, dry_run=args.dry_run, state=state)
        except (httpx.HTTPError, OSError) as e:
            log.error("iteration failed, entries on the router are left as-is: %s", e)
        except Exception:
            log.exception("iteration failed, entries on the router are left as-is")
        if args.once:
            return 0
        if time.monotonic() - last_oui > 86400:
            oui, last_oui = refresh(args.oui_file), time.monotonic()
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(cli())
