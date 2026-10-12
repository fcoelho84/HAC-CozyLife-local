# CozyLife LAN protocol

Derived from CozyLife Android `1.20.24` (`com.cozylife.common.BuildConfig`, `com.cozylife.net.lan.*`, `com.cozylife.net.device.abs.Device`).

## Constants

| Name | Value | Source |
|------|-------|--------|
| `UDP_BOARDCAST_ADDRESS` | `255.255.255.255` | `BuildConfig` |
| `UDP_BOARDCAST_PORT` | `6095` | `BuildConfig` |
| `WIFI_TCP_PORT` | `5555` | `BuildConfig` |
| `WIFI_CONFIG_NET_TCP_ADDRESS` | `192.168.4.1` | Soft-AP only |
| `WIFI_CONFIG_NET_TCP_PORT` | `5555` | Soft-AP only |
| `COZYLIFE_TCP_ADDRESS` | `8.135.109.78` | Cloud (not used by local HAC) |
| `COZYLIFE_TCP_PORT` | `8899` | Cloud |
| `MESSAGE_TAIL_SEPARATE` | `\r\n` | `Config` / Windows line separator |
| `TINY_DOMAIN_NAME` | `dohome.doiting.com` | H5 panels |

## UDP discovery

### Probe (app → LAN broadcast)

```text
{"cmd":0,"pv":0,"sn":"<unix_ms>","msg":{}}\r\n
```

- Destination: `255.255.255.255:6095`
- Socket: client datagram with `SO_BROADCAST`
- Devices reply to the **source port** of the probe

### Reply (device → app)

Outer JSON. Device identity lives under `msg`:

| Field | Meaning |
|-------|---------|
| `did` | Device id |
| `ip` | LAN IPv4 |
| `mac` | MAC |
| `pid` | Product id |
| `dtp` | Device type string (app net-type related) |
| `hv` | Hardware version |
| `sv` | Software / firmware version |
| `rssi` | Signal (int) |

After parse, app sets local TCP IP from `ip` and port to `WIFI_TCP_PORT` (`5555`).

## Local TCP control

- Host: device `ip`
- Port: `5555`
- Encoding: UTF-8
- Frame end: `\r\n`

### Envelope

Built by `SingleWifiDevice.wifiCmd`:

```json
{"msg": <object-or-payload>, "pv": 0, "cmd": <int>, "sn": "<string>"}
```

`msg` is embedded as JSON (not a string literal).

### Commands used for switches

| cmd | Purpose | Example `msg` |
|-----|---------|----------------|
| `0` | UDP search (also used on UDP) | `{}` |
| `2` | Query attributes | `{"attr":[1,2]}` |
| `3` | Set properties | `{"data":{"1":255},"attr":[1]}` |
| `4` | Restore factory | (avoid) |
| `5` | OTA | (avoid) |

### Power payloads (`Device`)

```json
{"data":{"1":0},"attr":[1]}
{"data":{"1":255},"attr":[1]}
```

Property `"1"` = power. App treats stored open state as `0` or `1` in cache, but wire ON value is **`255`**.

### Full ON example (LAN)

```text
{"msg":{"data":{"1":255},"attr":[1]},"pv":0,"cmd":3,"sn":"1710000000000"}\r\n
```

### Full OFF example (LAN)

```text
{"msg":{"data":{"1":0},"attr":[1]},"pv":0,"cmd":3,"sn":"1710000000000"}\r\n
```

### Status query example (LAN)

```text
{"msg":{"attr":[1,2]},"pv":0,"cmd":2,"sn":"1710000000000"}\r\n
```

Response shape is device-dependent. Parser should accept property `"1"` under `msg.data`, `data`, or nested equivalents; treat `0` as off and any other numeric as on.

## Cloud TCP (reference only)

Not required for this HAC.

```text
cmd=publish&device_id=<id>&topic=control_<id>&device_key=<key>&message={"cmd":3,"pv":0,"sn":"<sn>","msg":{...}}\r\n
cmd=subscribe&topic=device_<id>&from=control&device_id=<id>&device_key=<key>\r\n
cmd=keep
cmd=get&flag=<ms>&device_id=<id>&device_key=<key>
```

Needs account-bound `device_key`.

## BLE (reference only)

| Role | 16-bit UUID (expanded via app `UuidUtils.uuid16To128`) |
|------|--------------------------------------------------------|
| Service | `1910` |
| Write | `2b11` |
| Notify | `2b10` |

Same cmd `3` power payloads after BLE framing (`BlePackage`).

## Net type codes

| Code | Enum |
|------|------|
| `00` | WIFI |
| `01` | BLE |
| `02` | WIFI_BLE |
| `03` | G24 |
