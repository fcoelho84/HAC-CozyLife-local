# Architecture

## App stack (source APK)

- UI: React Native shell + mPaaS/Tiny H5 panels (`dohome.doiting.com`)
- Device I/O: Kotlin/Java in `com.cozylife.net.*` (not the small RN bundle)
- Transports: UDP LAN discovery, TCP LAN control, cloud TCP, BLE GATT, optional Zigbee-via-gateway

## Domain objects

```
Device (abstract)
  ├── SingleWifiDevice      # Wi‑Fi only — primary HAC target
  ├── SingleBleDevice       # BLE / child via gateway
  ├── WifiBleDevice         # dual; gateway parent
  └── Group*Device          # groups of endpoints
```

Power is property `"1"` on the device property map, not a separate switch type.

## Control paths for on/off

```
letDeviceOn / letDeviceOff
  ├── LAN TCP  ip:5555   wifiCmd(cmd=3, DEVICE_ON/OFF_MSG)
  ├── Cloud TCP 8.135.109.78:8899   cmd=publish + device_key
  └── BLE write UUID 2b11          same logical cmd 3
```

Local HAC implements the LAN TCP path only.

## Discovery path

```
WifiUdpServer.sendBroadcast
  → UDP {"cmd":0,...} to 255.255.255.255:6095
  ← replies with msg = UdpDeviceInfo
  → attachToDevice: localhost IP + port 5555
```

## HAC runtime (this repo)

```
custom_components/cozylife_local/
  discovery.py   # UDP probe/listen
  client.py      # TCP send/recv frames
  protocol.py    # payload builders/parsers
  coordinator.py # poll status
  switch.py      # HA switch entities
  config_flow.py # discover + register
```

Home Assistant talks only to LAN devices. Cloud/BLE remain documented for future work.
