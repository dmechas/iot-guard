from iot_guard.reconciler import Entry, make_comment, parse_mac, reconcile

MAC1, MAC2 = "AA:BB:CC:00:00:01", "AA:BB:CC:00:00:02"


def entry(id, address, mac=None, comment=None):
    if comment is None:
        comment = make_comment(mac, "test")
    return Entry(id=id, address=address, comment=comment)


def test_comment_roundtrip():
    assert parse_mac(make_comment(MAC1, "hostname ESP_1")) == MAC1


def test_parse_mac_ignores_foreign_comments():
    assert parse_mac("my manual rule") is None
    assert parse_mac("") is None


def test_adds_new_block():
    plan = reconcile({MAC1: "192.168.88.8"}, [])
    assert [(a.mac, a.address) for a in plan.add] == [(MAC1, "192.168.88.8")]
    assert not plan.remove and not plan.update


def test_nothing_to_do_when_in_sync():
    plan = reconcile({MAC1: "1.1.1.1"}, [entry("*1", "1.1.1.1", MAC1)])
    assert not plan.add and not plan.remove and not plan.update


def test_removes_entry_no_longer_desired():
    e = entry("*1", "1.1.1.1", MAC1)
    plan = reconcile({}, [e])
    assert plan.remove == [e]


def test_updates_changed_ip():
    e = entry("*1", "1.1.1.1", MAC1)
    plan = reconcile({MAC1: "1.1.1.2"}, [e])
    assert plan.update == [(e, "1.1.1.2")]
    assert not plan.add and not plan.remove


def test_foreign_entries_untouched():
    manual = entry("*9", "9.9.9.9", comment="manual")
    other_tool = entry("*8", "8.8.8.8", comment="other:thing")
    plan = reconcile({}, [manual, other_tool])
    assert not plan.add and not plan.remove and not plan.update


def test_duplicate_entries_for_one_mac_collapse_to_one():
    a, b = entry("*1", "1.1.1.1", MAC1), entry("*2", "1.1.1.1", MAC1)
    plan = reconcile({MAC1: "1.1.1.1"}, [a, b])
    assert plan.remove == [b]
    assert not plan.add and not plan.update


def test_new_block_ips_reported_for_connection_kill():
    plan = reconcile({MAC1: "1.1.1.1", MAC2: "2.2.2.2"}, [entry("*1", "1.1.1.1", MAC1)])
    assert plan.new_block_addresses == ["2.2.2.2"]
    plan = reconcile({MAC1: "1.1.1.9"}, [entry("*1", "1.1.1.1", MAC1)])
    assert plan.new_block_addresses == ["1.1.1.9"]
