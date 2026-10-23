from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime


log = logging.getLogger("iot_guard.config")


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


_LIST_KEYS = ("class_id_allow", "class_id_iot", "hostname_block", "iot_vendors", "ambiguous_vendors")
_MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")


def _parse_until(mac: str, value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(
        f"override {mac}: until must be a date and time, e.g. \"2026-10-23T20:00:00+01:00\", got {value!r}"
    )


def load_config(path) -> Rules:
    import yaml

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError("config must be a mapping")
    unknown = set(raw) - {"overrides", *_LIST_KEYS}
    if unknown:
        raise ValueError(f"unknown config key(s): {', '.join(sorted(unknown))}")

    overrides = {}
    for mac, spec in (raw.get("overrides") or {}).items():
        mac = normalize_mac(str(mac))
        if not _MAC_RE.match(mac):
            raise ValueError(f"override key {mac!r} is not a MAC address")
        spec = spec if isinstance(spec, dict) else {"action": spec}
        overrides[mac] = Override(
            action=spec.get("action", ""),
            note=spec.get("note", ""),
            until=_parse_until(mac, spec.get("until")),
        )

    lists = {}
    for k in _LIST_KEYS:
        value = raw.get(k) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{k} must be a list of strings")
        lists[k] = value
    for pattern in lists["hostname_block"]:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"invalid hostname_block regex {pattern!r}: {e}") from e
    return Rules(overrides=overrides, **lists)


class ConfigWatcher:
    """Reloads the config whenever the file changes. The initial load must succeed; after that a
    broken or missing file keeps the last good rules, so a typo can never unblock devices."""

    def __init__(self, path):
        self._path = path
        self._version = self._stat()
        self._rules = load_config(path)
        self._bad_version = None

    def _stat(self):
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def rules(self) -> Rules:
        version = self._stat()
        if version == self._version or version == self._bad_version:
            return self._rules
        try:
            rules = load_config(self._path)
        except Exception as e:
            self._bad_version = version
            log.error("config %s invalid, keeping previous rules: %s", self._path, e)
            return self._rules
        self._rules, self._version, self._bad_version = rules, version, None
        log.info("config %s reloaded", self._path)
        return rules
