# iot-guard

Automatically cuts IoT devices off from the internet on a MikroTik router (RouterOS 7.x).
Everything has internet unless it is classified as IoT. Blocked devices keep LAN access
(e.g. inverter ↔ Home Assistant) and get DNS/NTP from the router.

It polls the router's DHCP leases every 30 s, classifies each device, and keeps the
firewall address list `iot-blocked` in sync through the RouterOS REST API. A single
router rule drops traffic from that list to the WAN.

## How it works

Classification, first decisive match wins:

1. `overrides` in `config.yaml` (per MAC, `block` or `allow`, optionally time-limited)
2. Randomized (locally administered) MAC → allow (phones, laptops)
3. DHCP class-id matches `class_id_allow` → allow
4. Hostname matches a `hostname_block` regex → block
5. OUI vendor in `iot_vendors` → block; in `ambiguous_vendors` → block only with an
   IoT signal (`class_id_iot`, e.g. `udhcp*`)
6. Nothing matched → allow, logged once as `UNCLASSIFIED <mac> <vendor> <hostname>`

Only address-list entries whose comment starts with `iot-guard:` are ever touched.
Entries are static: if the service stops, blocked devices stay blocked. New devices are
simply not classified until it is back. When a device becomes blocked, its open
connections are dropped.

Known limits: the OUI only identifies the NIC vendor, randomized MACs carry no vendor,
and MAC spoofing defeats any MAC-based scheme. Fix misses with overrides. Devices keep
LAN access to each other; real isolation would need VLAN-capable APs.

## Router setup (one time)

Replace `<SERVER_IP>` with the machine running iot-guard.

```
/user group add name=iot-guard policy=read,write,api,rest-api
/user add name=iot-guard group=iot-guard address=<SERVER_IP>/32 password="<strong password>"
/certificate add name=local-ca common-name=local-ca key-usage=key-cert-sign,crl-sign
/certificate sign local-ca
/certificate add name=router-https common-name=192.168.88.1 subject-alt-name=IP:192.168.88.1
/certificate sign router-https ca=local-ca
/ip service set www-ssl certificate=router-https disabled=no address=<SERVER_IP>/32
/ip firewall filter add chain=forward src-address-list=iot-blocked out-interface-list=WAN action=drop comment="iot-guard" place-before=0
/system ntp client set enabled=yes mode=unicast servers=0.pool.ntp.org,1.pool.ntp.org,2.pool.ntp.org
/system ntp server set enabled=yes
/ip dhcp-server network set [find where comment=defconf] ntp-server=192.168.88.1
```

- The `iot-guard` drop rule must sit **above** the fasttrack rule, or fasttracked
  connections bypass it.
- Export the CA for the service: `/certificate export-certificate local-ca`, then copy
  `cert_export_local-ca.crt` to `certs/local-ca.pem`.
- `ROUTEROS_URL` must use the IP in the certificate (`https://192.168.88.1`), not a hostname.
- Moving to another server: update the `address=` of the user and of `www-ssl`.

## Configuration

Copy `config.example.yaml` to `config.yaml` (git-ignored) and edit. All rule lists live
there, so no code changes are needed. **The file is read at startup: restart after editing.**

Environment variables:

| Variable | Meaning |
| --- | --- |
| `ROUTEROS_URL` | e.g. `https://192.168.88.1` |
| `ROUTEROS_USER` / `ROUTEROS_PASSWORD` | the `iot-guard` router user |
| `ROUTEROS_CA_CERT` | path to the exported CA certificate |
| `IOT_GUARD_CONFIG` | config path (default `config.yaml`) |
| `IOT_GUARD_OUI` | where to cache the IEEE OUI list (default `/data/oui.csv`) |

### Overrides

```yaml
overrides:
  "A8:00:00:00:00:01": {action: block, note: "Huawei SUN2000 inverter"}
  "A8:BB:CC:00:00:01": {action: allow, note: "ESP dev board that needs internet"}
  # Temporary internet; reverts to normal classification after `until`
  "A8:00:00:00:00:04": {action: allow, until: "2026-10-23T20:00:00+01:00", note: "Shelly fw update"}
```

- `until` is only valid with `allow`. Use an explicit UTC offset: a time without one is
  read in the container's timezone, which is UTC by default.
- After `until`, the device falls back to normal classification and is blocked again
  within one poll, with its open connections dropped.
- A `block` override for a device with a static DHCP lease stays in force while the device
  is offline.

### Tuning

Run and look for `UNCLASSIFIED` lines. Add an override for a single device, or extend
`hostname_block` / `iot_vendors` for a class of devices. Vendor names are matched
case-insensitively but must otherwise equal the IEEE registry name.

## Running

Local, read-only checks (Python 3.12+):

```
uv venv && uv pip install -e '.[dev]'
export ROUTEROS_URL=https://192.168.88.1 ROUTEROS_USER=iot-guard \
       ROUTEROS_PASSWORD='…' ROUTEROS_CA_CERT=certs/local-ca.pem IOT_GUARD_OUI=./oui.csv

.venv/bin/iot-guard leases                                        # list leases with vendor
.venv/bin/iot-guard run --dry-run --once --config config.yaml     # show planned changes
.venv/bin/iot-guard run --once --config config.yaml               # apply once
```

Docker, on the server:

1. Put `config.yaml`, `certs/local-ca.pem` and a `.env` containing `ROUTEROS_PASSWORD=…`
   next to `compose.yaml`.
2. `compose.yaml` starts with `--dry-run`. Review the logs for a while, then change
   `command` to `["run"]`.
3. `docker compose up -d`. The container restarts with `unless-stopped`.

Logs go to stdout. At startup the service warns if the router has a global IPv6 address
or a `::/0` route, because the drop rule is IPv4-only.

## Verifying on the router

```
/ip firewall address-list print where list=iot-blocked
/ip firewall filter print stats where comment="iot-guard"        # counters should rise
/ip firewall connection print where src-address~"^<blocked-ip>:" # no WAN connections
```

Stopping the container leaves the entries in place. Restarting must not create duplicates.

## Development

```
.venv/bin/pytest
```

- `classifier.py`: pure classification
- `reconciler.py`: pure diff of desired vs. current address-list entries
- `routeros.py`: REST client (tests use `httpx.MockTransport`)
- `oui.py`: IEEE OUI database
- `config.py`: YAML loading and validation
- `main.py`: poll loop and CLI
