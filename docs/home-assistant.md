# Home Assistant custom component

## Install (HACS custom repository)

1. HACS → ⋮ → **Custom repositories**
2. URL: `https://github.com/fcoelho84/HAC-CozyLife-local`
3. Type: **Integration** → Add → download **CozyLife Local**
4. Restart Home Assistant
5. Settings → Devices & services → Add integration → **CozyLife Local**
6. Run discovery (or add IP manually) and select switches

Manual fallback: copy `custom_components/cozylife_local` into `<config>/custom_components/`.

HA host must be on the same L2/L3 network that can broadcast to `255.255.255.255:6095` and open TCP to device `:5555`. Isolated VLANs / client isolation break discovery and control.

## Domain

`cozylife_local`

## Entities

One `switch.*` per registered device.

- `turn_on` → property `"1" = 255` via TCP cmd `3`
- `turn_off` → property `"1" = 0` via TCP cmd `3`
- state → polled with TCP cmd `2` (`attr` 1 and 2)

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
      "port": 5555
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
- Single power channel (`"1"`). Multi-gang extras need more RE.
- No cloud pairing / `device_key`.
- No BLE.
- Optimistic UI if status parse fails after command; next poll corrects state when possible.
