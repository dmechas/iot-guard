from __future__ import annotations

import re
from dataclasses import dataclass, field

PREFIX = "iot-guard:"
_MAC_RE = re.compile(r"^iot-guard:([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})(?:\s|$)")


@dataclass(frozen=True)
class Entry:
    """An address-list entry on the router."""

    id: str
    address: str
    comment: str


@dataclass(frozen=True)
class Add:
    mac: str
    address: str


@dataclass
class Plan:
    add: list[Add] = field(default_factory=list)
    remove: list[Entry] = field(default_factory=list)
    update: list[tuple[Entry, str]] = field(default_factory=list)

    @property
    def new_block_addresses(self) -> list[str]:
        """Addresses that just became blocked; their open connections should be dropped."""
        return [a.address for a in self.add] + [ip for _, ip in self.update]

    def __bool__(self) -> bool:
        return bool(self.add or self.remove or self.update)


def make_comment(mac: str, reason: str) -> str:
    return f"{PREFIX}{mac} {reason}"


def parse_mac(comment: str) -> str | None:
    m = _MAC_RE.match(comment or "")
    return m.group(1).upper() if m else None


def reconcile(desired: dict[str, str], current: list[Entry]) -> Plan:
    """Diff desired {mac: ip} against owned entries. Foreign entries are never touched."""
    plan = Plan()
    seen: set[str] = set()
    for e in current:
        mac = parse_mac(e.comment)
        if mac is None:
            continue
        if mac not in desired or mac in seen:
            plan.remove.append(e)
            continue
        seen.add(mac)
        if e.address != desired[mac]:
            plan.update.append((e, desired[mac]))
    plan.add = [Add(m, ip) for m, ip in desired.items() if m not in seen]
    return plan
