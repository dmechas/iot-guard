import logging

from iot_guard.classifier import Lease
from iot_guard.config import Override, Rules
from iot_guard.main import State, cli, run_once
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
    r = FakeRouter([LAPTOP], [entry])
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert r.calls == [("remove", "*7")]


def test_unclassified_logged_once(caplog):
    r = FakeRouter([LAPTOP])
    state = State()
    with caplog.at_level(logging.INFO):
        run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False, state=state)
        run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False, state=state)
    assert sum("UNCLASSIFIED" in m for m in caplog.messages) == 1


def test_empty_lease_list_unblocks_nothing(caplog):
    entry = Entry("*7", "192.168.88.8", make_comment("AA:BB:CC:00:00:01", "x"))
    r = FakeRouter([], [entry])
    with caplog.at_level(logging.WARNING):
        run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert r.calls == []
    assert any("no DHCP leases" in m for m in caplog.messages)


class FlakyRouter(FakeRouter):
    def __init__(self, *a, fail_add=(), fail_drop=0, **kw):
        super().__init__(*a, **kw)
        self.fail_add = set(fail_add)
        self.fail_drop = fail_drop

    def add_blocked(self, list_name, address, comment):
        if address in self.fail_add:
            raise RuntimeError("boom")
        super().add_blocked(list_name, address, comment)

    def drop_connections(self, address):
        if self.fail_drop:
            self.fail_drop -= 1
            raise RuntimeError("boom")
        return super().drop_connections(address)


def test_one_failed_add_does_not_stop_the_others():
    r = FlakyRouter([INVERTER, ESP], fail_add={"192.168.88.8"})
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False)
    assert ("add", "192.168.88.20") in r.calls
    assert r.dropped == ["192.168.88.20"]


def test_failed_connection_drop_is_retried_next_run():
    r = FlakyRouter([ESP], fail_drop=1)
    state = State()
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False, state=state)
    assert r.dropped == []
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False, state=state)
    assert r.dropped == ["192.168.88.20"]
    run_once(r, NoVendor(), RULES, "iot-blocked", dry_run=False, state=state)
    assert r.dropped == ["192.168.88.20"]  # not repeated once it succeeded


class DownRouter:
    def __getattr__(self, name):
        def fail(*a, **kw):
            raise ConnectionError("router unreachable")
        return fail


def test_cli_survives_router_down_at_startup(tmp_path, monkeypatch):
    import iot_guard.main as m

    cfg = tmp_path / "c.yaml"
    cfg.write_text("{}\n")
    monkeypatch.setattr(m, "_router_from_env", lambda: DownRouter())
    monkeypatch.setattr(m, "refresh", lambda path: NoVendor())
    assert cli(["run", "--once", "--config", str(cfg)]) == 0


def test_password_read_from_file(tmp_path, monkeypatch):
    import iot_guard.main as m

    pw = tmp_path / "pw"
    pw.write_text("s3cret\n")
    monkeypatch.setenv("ROUTEROS_URL", "https://r")
    monkeypatch.setenv("ROUTEROS_USER", "u")
    monkeypatch.delenv("ROUTEROS_PASSWORD", raising=False)
    monkeypatch.setenv("ROUTEROS_PASSWORD_FILE", str(pw))
    seen = {}
    monkeypatch.setattr(m, "RouterOS", lambda url, user, password, **kw: seen.update(password=password))
    m._router_from_env()
    assert seen["password"] == "s3cret"
