from __future__ import annotations

import csv
import io
import logging
import os
import time
from pathlib import Path

import httpx

OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
MIN_ROWS = 10_000  # the real registry has ~38k MA-L rows; fewer means a truncated/bogus download
log = logging.getLogger("iot_guard.oui")


class OuiDb:
    def __init__(self, table: dict[str, str]):
        self._table = table

    @staticmethod
    def parse(text: str) -> dict[str, str]:
        reader = csv.DictReader(io.StringIO(text))
        if not {"Assignment", "Organization Name"} <= set(reader.fieldnames or ()):
            raise ValueError("not an IEEE OUI csv (missing columns)")
        return {row["Assignment"].upper(): row["Organization Name"] for row in reader}

    @classmethod
    def from_file(cls, path) -> OuiDb:
        return cls(cls.parse(Path(path).read_text(encoding="utf-8")))

    def vendor(self, mac: str) -> str:
        key = mac.upper().replace(":", "").replace("-", "")[:6]
        return self._table.get(key, "")

    @staticmethod
    def is_stale(path, max_age_s: float) -> bool:
        try:
            return time.time() - os.path.getmtime(path) > max_age_s
        except FileNotFoundError:
            return True


def _download() -> bytes:
    r = httpx.get(OUI_URL, timeout=60, follow_redirects=True, headers={"User-Agent": "iot-guard"})
    r.raise_for_status()
    return r.content


def refresh(path, max_age_s: float = 7 * 86400, *, fetch=_download, min_rows: int = MIN_ROWS) -> OuiDb:
    """Load the OUI db, re-downloading if stale. Never raises: an invalid download keeps the
    cached copy, and a missing or corrupt cache yields an empty db (vendor rules disabled)."""
    path = Path(path)
    if OuiDb.is_stale(path, max_age_s):
        try:
            content = fetch()
            rows = len(OuiDb.parse(content.decode("utf-8")))
            if rows < min_rows:
                raise ValueError(f"only {rows} rows")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(content)
            tmp.replace(path)
            log.info("OUI database updated (%d vendors)", rows)
        except Exception as e:
            log.warning("OUI download rejected or failed, keeping cached copy: %s", e)
    try:
        return OuiDb.from_file(path)
    except FileNotFoundError:
        log.warning("No OUI database available; vendor rules disabled")
    except Exception as e:
        log.warning("OUI cache %s unreadable (%s); vendor rules disabled", path, e)
    return OuiDb({})
