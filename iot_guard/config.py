from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def normalize_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


@dataclass(frozen=True)
class Override:
    action: str  # "block" | "allow"
    note: str = ""
    until: datetime | None = None  # allow-only; after this the override is ignored

    def __post_init__(self):
        if self.action not in ("block", "allow"):
            raise ValueError(f"override action must be block|allow, got {self.action!r}")
        if self.until is not None and self.action != "allow":
            raise ValueError("'until' is only valid for allow overrides")
        if self.until is not None and self.until.tzinfo is None:
            object.__setattr__(self, "until", self.until.astimezone())  # naive = local time


@dataclass
class Rules:
    overrides: dict[str, Override] = field(default_factory=dict)
    class_id_allow: list[str] = field(default_factory=list)  # fnmatch globs
    class_id_iot: list[str] = field(default_factory=list)  # fnmatch globs
    hostname_block: list[str] = field(default_factory=list)  # regexes
    iot_vendors: list[str] = field(default_factory=list)
    ambiguous_vendors: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.overrides = {normalize_mac(m): o for m, o in self.overrides.items()}


def load_config(path) -> Rules:
    import re

    import yaml

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    overrides = {}
    for mac, spec in (raw.get("overrides") or {}).items():
        spec = spec if isinstance(spec, dict) else {"action": spec}
        until = spec.get("until")
        if isinstance(until, str):
            until = datetime.fromisoformat(until)
        overrides[mac] = Override(
            action=spec.get("action", ""), note=spec.get("note", ""), until=until
        )
    rules = Rules(
        overrides=overrides,
        **{
            k: list(raw.get(k) or [])
            for k in (
                "class_id_allow",
                "class_id_iot",
                "hostname_block",
                "iot_vendors",
                "ambiguous_vendors",
            )
        },
    )
    for pattern in rules.hostname_block:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"invalid hostname_block regex {pattern!r}: {e}") from e
    return rules
