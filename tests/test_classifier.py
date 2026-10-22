import pytest

from iot_guard.classifier import Lease, classify
from iot_guard.config import Override, Rules

RULES = Rules(
    overrides={
        "AA:BB:CC:00:00:01": Override(action="block", note="static"),
        "AA:BB:CC:00:00:02": Override(action="allow", note="my esp dev board"),
    },
    class_id_allow=["android-dhcp-*", "MSFT 5.0"],
    class_id_iot=["udhcp*"],
    hostname_block=[r"^ESP[_-]", r"^shelly", r"^SUN2000"],
    iot_vendors=["Espressif Inc.", "Tuya Smart Inc."],
    ambiguous_vendors=["Realtek Semiconductor Corp."],
)


def lease(mac="10:20:30:40:50:60", host="", cls=""):
    return Lease(mac=mac, address="192.168.88.50", host_name=host, class_id=cls)


@pytest.mark.parametrize(
    "lse, vendor, block, reason_part",
    [
        # randomized (locally administered) MAC -> allow
        (lease(mac="32:00:00:00:00:01"), "", False, "randomized"),
        # IoT vendor OUI -> block
        (lease(), "Espressif Inc.", True, "vendor"),
        # ambiguous vendor alone -> allow
        (lease(), "Realtek Semiconductor Corp.", False, "unclassified"),
        # ambiguous vendor + embedded dhcp client -> block
        (lease(cls="udhcp 1.31.1"), "Realtek Semiconductor Corp.", True, "ambiguous"),
        # hostname rule -> block even without a vendor
        (lease(host="ESP_1A2B3C"), "", True, "hostname"),
        (lease(host="shelly1-ABC"), "", True, "hostname"),
        # inverter
        (lease(host="SUN2000-XX0000000000"), "Huawei Technologies", True, "hostname"),
        # android class-id beats an IoT-looking vendor
        (lease(cls="android-dhcp-14"), "Espressif Inc.", False, "class-id"),
        # udhcp alone is only a signal
        (lease(cls="udhcpc"), "Some Vendor", False, "unclassified"),
        # nothing matched
        (lease(host="laptop"), "Apple, Inc.", False, "unclassified"),
    ],
)
def test_classify(lse, vendor, block, reason_part):
    v = classify(lse, vendor, RULES)
    assert v.block is block
    assert any(reason_part in r for r in v.reasons), v.reasons


def test_override_block_beats_randomized_mac():
    v = classify(lease(mac="aa:bb:cc:00:00:01"), "", RULES)  # lowercase input
    assert v.block is True
    assert any("override" in r for r in v.reasons)


def test_override_allow_beats_iot_vendor():
    v = classify(lease(mac="AA:BB:CC:00:00:02"), "Espressif Inc.", RULES)
    assert v.block is False
    assert any("override" in r for r in v.reasons)


def test_unclassified_flag():
    assert classify(lease(host="laptop"), "Apple, Inc.", RULES).unclassified is True
    assert classify(lease(), "Espressif Inc.", RULES).unclassified is False


def test_vendor_match_is_case_insensitive():
    assert classify(lease(), "ESPRESSIF INC.", RULES).block is True
    v = classify(lease(cls="udhcp 1"), "REALTEK SEMICONDUCTOR CORP.", RULES)
    assert v.block is True


from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def timed_rules(until):
    return Rules(
        overrides={"A8:BB:CC:00:00:03": Override("allow", "shelly fw update", until=until)},
        iot_vendors=["Espressif Inc."],
    )


def test_timed_override_active_before_expiry():
    r = timed_rules(NOW + timedelta(hours=1))
    v = classify(lease(mac="A8:BB:CC:00:00:03"), "Espressif Inc.", r, now=NOW)
    assert v.block is False
    assert any("override" in x and "until" in x for x in v.reasons)


def test_timed_override_expired_falls_back_to_rules():
    r = timed_rules(NOW - timedelta(minutes=1))
    v = classify(lease(mac="A8:BB:CC:00:00:03"), "Espressif Inc.", r, now=NOW)
    assert v.block is True
    assert not any("override" in x for x in v.reasons)
