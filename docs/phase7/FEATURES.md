# Features — Phase 7 (MVP = all of these; Wear OS is a fast-follow, not a stretch goal squeezed into this wave)

1. **[MVP] Authenticated beacon foreground service** — `BeaconService`, survives screen lock, produces the exact Phase 6 wire format.
2. **[MVP] Scanner foreground service** — `ScannerService`, streams `reading`/`lost` messages into the existing mesh over WebSocket.
3. **[MVP] QR pairing (join side)** — CameraX/ML Kit scan → mutual Ed25519 exchange → realm key received and persisted.
4. **[MVP] Settings screen** — paired status, realm key version, live scanned-peer RSSI table.
5. **[MVP] Cross-language beacon test vector** — `shared/` module unit test proving byte-exact parity with `aether-bridge/beacon_auth.py`.
6. **[Deferred, fast-follow] Wear OS companion** — beacon-only, reuses `shared/BeaconAuth.kt` as-is, small diff once the phone app is proven.
7. **[Deferred] Wake-word / assistant integration** — Phase 8, not this wave.
8. **[Deferred] Play Store signing/release** — debug `adb install` only, this phase.
