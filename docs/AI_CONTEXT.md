# AI context — CozyLife local switch control

Use this file as the primary brief before changing protocol, discovery, or Home Assistant entities.

## Goal

Local Home Assistant custom component (`cozylife_local`) that:

1. Discovers CozyLife Wi‑Fi devices on LAN (UDP).
2. Registers them as switch entities.
3. Turns them on/off over local TCP (no cloud required for control).

## Source of truth

Reverse-engineered from CozyLife Android app `1.20.24` (`CozyLife_1.20.24_APKPure.apk`), packages `com.cozylife.net.*`.

Related detailed docs:

- `protocol.md` — wire formats, ports, payloads
- `architecture.md` — app stack and device model
- `home-assistant.md` — HAC layout and behavior

## Non-negotiable protocol facts

| Item | Value |
|------|-------|
| UDP discovery host | `255.255.255.255` |
| UDP discovery port | `6095` |
| UDP probe | `{"cmd":0,"pv":0,"sn":"<ms>","msg":{}}\r\n` |
| UDP reply | JSON; device fields in `.msg` (`did`, `ip`, `mac`, `pid`, `dtp`, `hv`, `sv`, `rssi`) |
| Local TCP control port | `5555` |
| TCP framing | UTF-8 JSON + `\r\n` (`MESSAGE_TAIL_SEPARATE`) |
| Set property cmd | `3` |
| Power property id | `"1"` |
| OFF | `{"data":{"1":0},"attr":[1]}` |
| ON | `{"data":{"1":255},"attr":[1]}` |
| Local TCP set body | `{"msg":<OFF/ON object>,"pv":0,"cmd":3,"sn":"<ms>"}` |
| Status query cmd | `2` with msg `{"attr":[1,2]}` |

`msg` in the TCP envelope is a **JSON object**, not a quoted string (app concatenates raw JSON).

## Device model (switches)

There is no dedicated “light switch” class. A wall switch is a generic `Device` whose property `"1"` is power (`0` off, `255` on; UI often treats non-zero as on).

Net types in app: `WIFI` / `BLE` / `WIFI_BLE` / `G24`. This HAC targets **Wi‑Fi LAN** devices that answer UDP `6095` and accept TCP `5555`.

## Out of scope (for now)

- Cloud TCP `8.135.109.78:8899` (needs `device_key`)
- BLE GATT (`1910` / write `2b11` / notify `2b10`)
- Soft-AP provisioning `192.168.4.1:5555`
- Multi-gang endpoints beyond property `"1"`
- Zigbee-behind-gateway children (`sid` / parent publish)

## When changing code

1. Keep framing `\r\n`.
2. Keep property `"1"` values `0` / `255`.
3. Prefer local TCP; do not require cloud login for on/off.
4. Discovery must set TCP port to `5555` after UDP hit (matches app `BuildConfig.WIFI_TCP_PORT`).
5. Update `protocol.md` if any wire constant changes.
