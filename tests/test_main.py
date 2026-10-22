import logging

from iot_guard.classifier import Lease
from iot_guard.config import Override, Rules
from iot_guard.main import run_once
from iot_guard.reconciler import Entry, make_comment


class FakeRouter:
    def __init__(self, leases, entries=()):
        self._leases = leases
        self.entries = list(entries)
        self.dropped = []
        self.calls = []

    def leases(self):
        return self._leases

    def blocked_entries(self, list_name):
        return list(self.entries)

    def add_blocked(self, list_name, address, comment):
        self.calls.append(("add", address))
        self.entries.append(Entry(f"*{len(self.entries)}", address, comment))

    def update_blocked_address(self, id, address):
        self.calls.append(("update", id, address))

    def remove_blocked(self, id):
        self.calls.append(("remove", id))
        self.entries = [e for e in self.entries if e.id != id]

    def drop_connections(self, address):
        self.dropped.append(address)
        return 1


class NoVendor:
    def vendor(self, mac):
        return ""


RULES = Rules(
    overrides={"AA:BB:CC:00:00:01": Override("block", "inverter")},
    hostname_block=["^ESP"],
)
INVERTER = Lease("AA:BB:CC:00:00:01", "192.168.88.8", "SUN2000")
ESP = Lease("10:20:30:40:50:60", "192.168.88.20", "ESP_1")
LAPTOP = Lease("10:20:30:40:50:99", "192.168.88.30", "laptop")


def test_blocks_iot_and_kills_connections():
    r = FakeRouter([INVERTER, ESP, LAPTOP])
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert sorted(c[1] for c in r.calls) == ["192.168.88.20", "192.168.88.8"]
    assert sorted(r.dropped) == ["192.168.88.20", "192.168.88.8"]


def test_second_run_is_idempotent():
    r = FakeRouter([INVERTER, ESP])
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    r.calls.clear()
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert r.calls == []


def test_dry_run_changes_nothing():
    r = FakeRouter([INVERTER])
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=True)
    assert r.calls == [] and r.dropped == []


def test_allow_override_removes_entry():
    entry = Entry("*7", "192.168.88.20", make_comment("10:20:30:40:50:60", "x"))
    rules = Rules(overrides={"10:20:30:40:50:60": Override("allow")})
    r = FakeRouter([ESP], [entry])
    run_once(r, NoVendor(), rules, "iot-blocked", dry_run=False)
    assert r.calls == [("remove", "*7")]


def test_override_block_with_inactive_static_lease_stays_blocked():
    entry = Entry("*7", "192.168.88.8", make_comment("AA:BB:CC:00:00:01", "x"))
    offline = Lease("AA:BB:CC:00:00:01", "192.168.88.8", "", active=False)
    r = FakeRouter([offline], [entry])
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert r.calls == []


def test_inactive_lease_of_non_override_device_is_not_blocked():
    offline = Lease("10:20:30:40:50:60", "192.168.88.20", "ESP_1", active=False)
    r = FakeRouter([offline])
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert r.calls == []


def test_entry_for_device_with_no_lease_at_all_is_removed():
    entry = Entry("*7", "192.168.88.20", make_comment("10:20:30:40:50:60", "x"))
    r = FakeRouter([], [entry])
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert r.calls == [("remove", "*7")]


def test_unclassified_logged_once(caplog):
    r = FakeRouter([LAPTOP])
    seen = set()
    with caplog.at_level(logging.INFO):
        run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False, logged=seen)
        run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False, logged=seen)
    assert sum("UNCLASSIFIED" in m for m in caplog.messages) == 1
