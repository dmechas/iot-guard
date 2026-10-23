import os
import time

import pytest

from iot_guard.config import load_config
from iot_guard.oui import OuiDb, refresh

CSV = (
    "Registry,Assignment,Organization Name,Organization Address\n"
    'MA-L,24D7EB,"Espressif Inc.","Shanghai CN"\n'
    "MA-L,00E04C,Realtek Semiconductor Corp.,Hsinchu TW\n"
)


def test_oui_lookup(tmp_path):
    p = tmp_path / "oui.csv"
    p.write_text(CSV)
    db = OuiDb.from_file(p)
    assert db.vendor("24:d7:eb:12:34:56") == "Espressif Inc."
    assert db.vendor("00-E0-4C-00-00-01") == "Realtek Semiconductor Corp."
    assert db.vendor("11:22:33:44:55:66") == ""


def test_oui_stale(tmp_path):
    p = tmp_path / "oui.csv"
    assert OuiDb.is_stale(p, max_age_s=100)  # missing
    p.write_text(CSV)
    assert not OuiDb.is_stale(p, max_age_s=100)
    os.utime(p, (time.time() - 1000, time.time() - 1000))
    assert OuiDb.is_stale(p, max_age_s=100)


def test_load_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        """
overrides:
  "aa:bb:cc:00:00:01": {action: block, note: inverter}
class_id_allow: ["android-dhcp-*"]
hostname_block: ["^ESP"]
iot_vendors: ["Espressif Inc."]
"""
    )
    rules = load_config(p)
    assert rules.overrides["AA:BB:CC:00:00:01"].action == "block"
    assert rules.class_id_allow == ["android-dhcp-*"]
    assert rules.ambiguous_vendors == []


def test_load_config_rejects_bad_action_and_regex(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text('overrides:\n  "aa:bb:cc:00:00:01": {action: maybe}\n')
    with pytest.raises(ValueError):
        load_config(p)
    p.write_text('hostname_block: ["(unclosed"]\n')
    with pytest.raises(ValueError):
        load_config(p)


def test_shipped_example_config_loads():
    rules = load_config("config.example.yaml")
    assert "Espressif Inc." in rules.iot_vendors


def test_load_config_until_naive_and_aware(tmp_path):
    from datetime import timezone

    p = tmp_path / "c.yaml"
    p.write_text(
        'overrides:\n'
        '  "aa:bb:cc:00:00:01": {action: allow, until: 2026-10-23 18:00}\n'
        '  "aa:bb:cc:00:00:02": {action: allow, until: "2026-10-23T18:00:00+00:00"}\n'
    )
    o = load_config(p).overrides
    assert o["AA:BB:CC:00:00:01"].until.tzinfo is not None  # naive -> local tz
    assert o["AA:BB:CC:00:00:02"].until.astimezone(timezone.utc).hour == 18


def test_until_only_valid_for_allow(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text('overrides:\n  "aa:bb:cc:00:00:01": {action: block, until: 2026-10-23 18:00}\n')
    with pytest.raises(ValueError):
        load_config(p)


def test_refresh_rejects_invalid_download_and_keeps_cache(tmp_path):
    p = tmp_path / "oui.csv"
    p.write_text(CSV)
    os.utime(p, (0, 0))  # stale
    db = refresh(p, fetch=lambda: b"<html>maintenance</html>\n", min_rows=1)
    assert db.vendor("24:D7:EB:00:00:00") == "Espressif Inc."
    assert p.read_text() == CSV


def test_refresh_rejects_truncated_download(tmp_path):
    p = tmp_path / "oui.csv"
    db = refresh(p, fetch=lambda: CSV.encode(), min_rows=1000)
    assert db.vendor("24:D7:EB:00:00:00") == ""
    assert not p.exists()


def test_refresh_installs_valid_download(tmp_path):
    p = tmp_path / "oui.csv"
    db = refresh(p, fetch=lambda: CSV.encode(), min_rows=1)
    assert db.vendor("24:D7:EB:00:00:00") == "Espressif Inc."
    assert p.read_text() == CSV


def test_refresh_survives_corrupt_cache(tmp_path):
    p = tmp_path / "oui.csv"
    p.write_text("garbage,header\nx,y\n")
    db = refresh(p, fetch=lambda: (_ for _ in ()).throw(OSError("offline")), min_rows=1)
    assert db.vendor("24:D7:EB:00:00:00") == ""


@pytest.mark.parametrize(
    "yaml_text, msg",
    [
        ("hostname_blocks: ['^ESP']\n", "unknown"),
        ('overrides:\n  "A8:00:00:00:00": {action: block}\n', "MAC"),
        ('overrides:\n  "A8:00:00:00:00:04": {action: allow, until: 2026-10-23}\n', "until"),
        ('overrides:\n  "A8:00:00:00:00:04": {action: allow, until: "tomorrow"}\n', "until"),
        ("iot_vendors: Espressif Inc.\n", "list"),
    ],
)
def test_load_config_rejects_mistakes(tmp_path, yaml_text, msg):
    p = tmp_path / "c.yaml"
    p.write_text(yaml_text)
    with pytest.raises(ValueError, match=msg):
        load_config(p)


def _write(p, text, mtime):
    p.write_text(text)
    os.utime(p, (mtime, mtime))  # explicit, so tests don't depend on mtime resolution


def test_config_watcher_picks_up_edits(tmp_path):
    from iot_guard.config import ConfigWatcher

    p = tmp_path / "c.yaml"
    _write(p, "iot_vendors: [A]\n", 1000)
    w = ConfigWatcher(p)
    assert w.rules().iot_vendors == ["A"]
    _write(p, "iot_vendors: [B]\n", 2000)
    assert w.rules().iot_vendors == ["B"]


def test_config_watcher_keeps_last_good_rules_on_bad_edit(tmp_path, caplog):
    import logging

    from iot_guard.config import ConfigWatcher

    p = tmp_path / "c.yaml"
    _write(p, "iot_vendors: [A]\n", 1000)
    w = ConfigWatcher(p)
    _write(p, "iot_vendorz: [B]\n", 2000)
    with caplog.at_level(logging.ERROR):
        assert w.rules().iot_vendors == ["A"]
        assert w.rules().iot_vendors == ["A"]
    assert sum("keeping previous" in m for m in caplog.messages) == 1  # logged once per bad version
    _write(p, "iot_vendors: [C]\n", 3000)
    assert w.rules().iot_vendors == ["C"]


def test_config_watcher_keeps_rules_if_file_disappears(tmp_path):
    from iot_guard.config import ConfigWatcher

    p = tmp_path / "c.yaml"
    _write(p, "iot_vendors: [A]\n", 1000)
    w = ConfigWatcher(p)
    p.unlink()
    assert w.rules().iot_vendors == ["A"]


def test_config_watcher_fails_fast_on_bad_initial_config(tmp_path):
    from iot_guard.config import ConfigWatcher

    p = tmp_path / "c.yaml"
    _write(p, "nope: 1\n", 1000)
    with pytest.raises(ValueError):
        ConfigWatcher(p)
