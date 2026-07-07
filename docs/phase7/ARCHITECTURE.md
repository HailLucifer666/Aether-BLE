# Architecture — Phase 7: Android Node App

## Module layout (new `aether-android/` at repo root)

```
aether-android/
├── settings.gradle.kts        # includes :app, :shared
├── build.gradle.kts
├── local.properties           # sdk.dir, gitignored
├── shared/                     # pure Kotlin, JVM-testable, no Android framework deps
│   ├── BeaconAuth.kt           # build/verify 19-byte payload - mirrors aether-bridge/beacon_auth.py exactly
│   ├── PairingClient.kt        # scanning-side counterpart to Phase 6's pairing.py offering side
│   └── src/test/...            # cross-language test vector lives here
└── app/                        # Android application module
    ├── AndroidManifest.xml     # FOREGROUND_SERVICE, BLUETOOTH_ADVERTISE/SCAN/CONNECT, CAMERA permissions
    ├── BeaconService.kt        # foreground service, BluetoothLeAdvertiser, rotates counter, persists it
    ├── ScannerService.kt       # foreground service, BluetoothLeScanner, forwards readings over WebSocket
    ├── PairingActivity.kt      # CameraX + ML Kit QR scan → PairingClient → realm key received
    └── SettingsScreen.kt       # Compose UI: paired status, realm version, live scanned-peer RSSI table
```

## Data flow — beacon (mirrors bridge.py's verification side)

```
BeaconService (foreground)
  → BeaconAuth.buildPayload(realmKey, uidHash, counter)   [shared/BeaconAuth.kt]
  → BluetoothLeAdvertiser.startAdvertising(manufacturerData = payload, id = 0xFFFF)
  → counter persisted to app-private storage (DataStore), incremented each rotation
     (mirrors Phase 6's confirmed decision: counter survives process/device restart)
```

## Data flow — scanner (produces what bridge.py already consumes)

```
ScannerService (foreground)
  → BluetoothLeScanner.startScan() → onScanResult(result)
  → extract manufacturerSpecificData[0xFFFF]
  → build the same "reading"/"lost" JSON shape aether-bridge/messages.py defines
     (field names are a locked contract per messages.py's own docstring - Android
     is just another producer of that same contract, not a new consumer format)
  → OkHttp WebSocket → aggregator (same endpoint bridge.py instances connect to)
```

## Data flow — pairing (join side, completing what Phase 6 started)

```
PairingActivity: CameraX scans QR → decodes {pubkey, mdns_name, realm_invite_token}
  → PairingClient connects to that peer's local TCP listener (pairing.py's PairingCeremony)
  → mutual Ed25519 exchange (Tink)
  → receives realm_key + realm_key_v → persisted to DataStore (Android's equivalent
     of aether-bridge's ~/.aether/realm.json)
```

## What is explicitly NOT built here
Wear OS module (`aether-android/wear/`, fast-follow — same `shared` module, beacon-only, no scanner/pairing UI), wake-word pipeline, any change to `aether-bridge/` Python code (Phase 7 is a pure consumer of Phase 6's already-shipped wire contract).

## Key risk called out by the council Skeptic
`shared/BeaconAuth.kt`'s HMAC construction must byte-for-byte match `aether-bridge/beacon_auth.py`. The checked-in test vector in PRD.md is the guardrail — if the Kotlin implementation produces a different hex string for those exact inputs, it is wrong, full stop, regardless of how reasonable the code looks.
