from __future__ import annotations

import ipaddress

import httpx

from .classifier import Lease
from .reconciler import Entry

LEASE_PATH = "/rest/ip/dhcp-server/lease"
ADDRLIST_PATH = "/rest/ip/firewall/address-list"
CONN_PATH = "/rest/ip/firewall/connection"


class RouterOS:
    """Thin RouterOS 7 REST client."""

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        *,
        verify: bool | str = True,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            auth=(user, password),
            verify=verify,
            transport=transport,
            timeout=timeout,
        )

    def _get(self, path: str, **params) -> list[dict]:
        r = self._http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def leases(self) -> list[Lease]:
        return [
            Lease(
                mac=row["mac-address"],
                address=row["address"],
                host_name=row.get("host-name", ""),
                class_id=row.get("class-id", ""),
                active=row.get("status") == "bound",
            )
            for row in self._get(LEASE_PATH)
            if "mac-address" in row
            and (row.get("status") == "bound" or row.get("dynamic") == "false")
        ]

    def blocked_entries(self, list_name: str) -> list[Entry]:
        return [
            Entry(id=row[".id"], address=row["address"], comment=row.get("comment", ""))
            for row in self._get(ADDRLIST_PATH, list=list_name)
        ]

    def add_blocked(self, list_name: str, address: str, comment: str) -> None:
        r = self._http.put(
            ADDRLIST_PATH, json={"list": list_name, "address": address, "comment": comment}
        )
        r.raise_for_status()

    def update_blocked_address(self, entry_id: str, address: str) -> None:
        self._http.patch(f"{ADDRLIST_PATH}/{entry_id}", json={"address": address}).raise_for_status()

    def remove_blocked(self, entry_id: str) -> None:
        self._http.delete(f"{ADDRLIST_PATH}/{entry_id}").raise_for_status()

    def drop_connections(self, address: str) -> int:
        """Remove tracked connections originating from exactly `address`."""
        n = 0
        # src-address includes the port and the REST query syntax has no prefix match,
        # so filter locally but only fetch the two columns we need.
        for row in self._get(CONN_PATH, **{".proplist": ".id,src-address"}):
            if row.get("src-address", "").rsplit(":", 1)[0] == address:
                r = self._http.delete(f"{CONN_PATH}/{row['.id']}")
                if r.status_code == 404:
                    continue  # connection closed between listing and delete
                r.raise_for_status()
                n += 1
        return n

    def ipv6_internet_problems(self) -> list[str]:
        """Describe signs of IPv6 internet access (which the IPv4-only block rule wouldn't stop)."""
        problems = []
        for row in self._get("/rest/ipv6/address"):
            addr = row.get("address", "")
            try:
                is_global = ipaddress.ip_interface(addr).ip.is_global
            except ValueError:
                continue
            if is_global:
                problems.append(f"global IPv6 address {addr}")
        for row in self._get("/rest/ipv6/route"):
            if row.get("dst-address") == "::/0":
                problems.append("IPv6 default route ::/0 present")
        return problems
