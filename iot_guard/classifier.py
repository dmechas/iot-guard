from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase

from .config import Rules, normalize_mac


@dataclass(frozen=True)
class Lease:
    mac: str
    address: str
    host_name: str = ""
    class_id: str = ""
    active: bool = True  # False: static lease whose device is currently offline


@dataclass(frozen=True)
class Verdict:
    block: bool
    reasons: list[str] = field(default_factory=list)
    unclassified: bool = False


def _is_randomized(mac: str) -> bool:
    return bool(int(mac.split(":")[0], 16) & 0x02)


def _matches_any(globs: list[str], value: str) -> str | None:
    return next((g for g in globs if fnmatchcase(value, g)), None)


def classify(lease: Lease, vendor: str, rules: Rules, now: datetime | None = None) -> Verdict:
    now = now or datetime.now().astimezone()
    mac = normalize_mac(lease.mac)

    override = rules.overrides.get(mac)
    if override and (override.until is None or now < override.until):
        note = f" ({override.note})" if override.note else ""
        until = f" until {override.until:%Y-%m-%d %H:%M}" if override.until else ""
        return Verdict(override.action == "block", [f"override: {override.action}{until}{note}"])

    if _is_randomized(mac):
        return Verdict(False, ["randomized MAC"])

    if g := _matches_any(rules.class_id_allow, lease.class_id):
        return Verdict(False, [f"class-id {lease.class_id!r} matches allow {g!r}"])

    iot_signal = _matches_any(rules.class_id_iot, lease.class_id)

    for pattern in rules.hostname_block:
        if lease.host_name and re.search(pattern, lease.host_name):
            return Verdict(True, [f"hostname {lease.host_name!r} matches {pattern!r}"])

    vendor_key = vendor.casefold()
    if vendor_key in {v.casefold() for v in rules.iot_vendors}:
        return Verdict(True, [f"vendor {vendor!r} is IoT"])

    if vendor_key in {v.casefold() for v in rules.ambiguous_vendors} and iot_signal:
        return Verdict(
            True, [f"ambiguous vendor {vendor!r} + class-id {lease.class_id!r} IoT signal"]
        )

    return Verdict(False, ["unclassified"], unclassified=True)
