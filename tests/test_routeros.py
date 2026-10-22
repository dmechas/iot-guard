import json

import httpx

from iot_guard.reconciler import Entry
from iot_guard.routeros import RouterOS


def make(handler):
    return RouterOS("https://r", "u", "p", transport=httpx.MockTransport(handler))


def test_leases_parsed_and_dynamic_unbound_skipped():
    def h(req):
        assert req.url.path == "/rest/ip/dhcp-server/lease"
        assert req.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json=[
            {".id": "*1", "mac-address": "AA:BB:CC:00:00:01", "address": "192.168.88.8",
             "host-name": "SUN2000", "class-id": "udhcp 1.2", "status": "bound"},
            {".id": "*2", "mac-address": "AA:BB:CC:00:00:02", "address": "192.168.88.9",
             "status": "waiting"},
            {".id": "*3", "address": "192.168.88.10", "status": "waiting"},
            {".id": "*4", "mac-address": "AA:BB:CC:00:00:04", "address": "192.168.88.11",
             "status": "waiting", "dynamic": "false"},
        ])

    leases = make(h).leases()
    assert [(l.mac, l.address, l.host_name, l.class_id, l.active) for l in leases] == [
        ("AA:BB:CC:00:00:01", "192.168.88.8", "SUN2000", "udhcp 1.2", True),
        ("AA:BB:CC:00:00:04", "192.168.88.11", "", "", False),  # static, offline
    ]


def test_blocked_entries():
    def h(req):
        assert req.url.path == "/rest/ip/firewall/address-list"
        assert req.url.params["list"] == "iot-blocked"
        return httpx.Response(200, json=[{".id": "*A", "address": "1.1.1.1", "comment": "c"}])

    assert make(h).blocked_entries("iot-blocked") == [Entry("*A", "1.1.1.1", "c")]


def test_add_update_remove_requests():
    seen = []

    def h(req):
        seen.append((req.method, req.url.path, json.loads(req.content or "null")))
        return httpx.Response(200, json={})

    r = make(h)
    r.add_blocked("iot-blocked", "1.1.1.1", "iot-guard:X")
    r.update_blocked_address("*A", "2.2.2.2")
    r.remove_blocked("*B")
    assert seen == [
        ("PUT", "/rest/ip/firewall/address-list",
         {"list": "iot-blocked", "address": "1.1.1.1", "comment": "iot-guard:X"}),
        ("PATCH", "/rest/ip/firewall/address-list/*A", {"address": "2.2.2.2"}),
        ("DELETE", "/rest/ip/firewall/address-list/*B", None),
    ]


def test_drop_connections_only_for_exact_source_ip():
    deleted = []

    def h(req):
        if req.method == "GET":
            return httpx.Response(200, json=[
                {".id": "*1", "src-address": "192.168.88.8:5000"},
                {".id": "*2", "src-address": "192.168.88.80:5000"},  # prefix trap
                {".id": "*3", "src-address": "192.168.88.8:6000"},
            ])
        deleted.append(req.url.path)
        return httpx.Response(204)

    n = make(h).drop_connections("192.168.88.8")
    assert n == 2
    assert deleted == ["/rest/ip/firewall/connection/*1", "/rest/ip/firewall/connection/*3"]


def test_ipv6_internet_detected():
    def h(req):
        if req.url.path.endswith("/ipv6/address"):
            return httpx.Response(200, json=[{"address": "fe80::1/64"}, {"address": "2a00:1450::1/64"}])
        return httpx.Response(200, json=[{"dst-address": "::/0"}])

    problems = make(h).ipv6_internet_problems()
    assert any("2a00:1450::1/64" in p for p in problems)
    assert any("::/0" in p for p in problems)


def test_ipv6_link_local_only_is_fine():
    def h(req):
        if req.url.path.endswith("/ipv6/address"):
            return httpx.Response(200, json=[{"address": "fe80::1/64"}])
        return httpx.Response(200, json=[{"dst-address": "fe80::/64"}])

    assert make(h).ipv6_internet_problems() == []


def test_ipv6_loopback_and_ula_are_not_internet():
    def h(req):
        if req.url.path.endswith("/ipv6/address"):
            return httpx.Response(200, json=[
                {"address": "::1/128"}, {"address": "fe80::1/64"}, {"address": "fd12:3456::1/64"},
            ])
        return httpx.Response(200, json=[])

    assert make(h).ipv6_internet_problems() == []
