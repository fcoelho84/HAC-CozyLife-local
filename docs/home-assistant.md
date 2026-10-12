# Home Assistant custom component

## Install (HACS custom repository)

1. HACS → ⋮ → **Custom repositories**
2. URL: `https://github.com/fcoelho84/HAC-CozyLife-local`
3. Type: **Integration** → Add → download **CozyLife Local**
4. Restart Home Assistant
5. Settings → Devices & services → Add integration → **CozyLife Local**
6. Run discovery (or add IP manually), select switches, set **channels** per device

Manual fallback: copy `custom_components/cozylife_local` into `<config>/custom_components/`.

HA host must be on the same L2/L3 network that can broadcast to `255.255.255.255:6095` and open TCP to device `:5555`. Isolated VLANs / client isolation break discovery and control.

## Domain

`cozylife_local`

## Entities

One `switch.*` **per channel**.

- `channels=1`: `turn_on` → `"1"=255`, `turn_off` → `"1"=0`
- `channels=N`: each entity toggles bit `N-1` on DPID `"1"` (read-modify-write bitmask)
- state → polled with TCP cmd `2` (`attr` 1 and 2)

Options flow: change channel count without re-adding devices.

## Config entry data

```json
{
  "devices": [
    {
      "did": "<device id>",
      "ip": "192.168.x.x",
      "mac": "aa:bb:cc:dd:ee:ff",
      "name": "optional label",
      "pid": "optional",
      "dtp": "optional",
      "port": 5555,
      "channels": 2
    }
  ]
}
```

## Network requirements

| Direction | Proto | Port | Notes |
|-----------|-------|------|-------|
| HA → broadcast | UDP | 6095 | discovery probe |
| device → HA | UDP | ephemeral | discovery reply |
| HA → device | TCP | 5555 | control / status |

## Limitations

- Wi‑Fi CozyLife devices only (UDP responders).
- Channel count is configured (LAN discovery does not return dpid list).
- No cloud pairing / `device_key`.
- No BLE.
- Optimistic UI if status parse fails after command; next poll corrects state when possible.
