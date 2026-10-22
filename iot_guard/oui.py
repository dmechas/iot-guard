from __future__ import annotations

import csv
import logging
import os
import time
from pathlib import Path

import httpx

OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
log = logging.getLogger("iot_guard.oui")


class OuiDb:
    def __init__(self, table: dict[str, str]):
        self._table = table

    @classmethod
    def from_file(cls, path) -> OuiDb:
        with open(path, newline="", encoding="utf-8") as f:
            return cls({row["Assignment"].upper(): row["Organization Name"] for row in csv.DictReader(f)})

    def vendor(self, mac: str) -> str:
        key = mac.upper().replace(":", "").replace("-", "")[:6]
        return self._table.get(key, "")

    @staticmethod
    def is_stale(path, max_age_s: float) -> bool:
        try:
            return time.time() - os.path.getmtime(path) > max_age_s
        except FileNotFoundError:
            return True


def refresh(path, max_age_s: float = 7 * 86400) -> OuiDb:
    """Load the OUI db, re-downloading if stale. A failed download falls back to the cached copy."""
    path = Path(path)
    if OuiDb.is_stale(path, max_age_s):
        try:
            r = httpx.get(OUI_URL, timeout=60, follow_redirects=True, headers={"User-Agent": "iot-guard"})
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(path)
            log.info("OUI database updated (%d bytes)", len(r.content))
        except Exception as e:
            log.warning("OUI download failed: %s", e)
    if not path.exists():
        log.warning("No OUI database available; vendor rules disabled")
        return OuiDb({})
    return OuiDb.from_file(path)
