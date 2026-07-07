# PRD — Aether Phase 7: Android Node App (Phone)

## Problem
The only beacon today is nRF Connect, a third-party debug app that stops advertising on screen lock and isn't part of Aether. Phase 6 built the authenticated beacon wire format and the pairing/discovery protocol server-side, but nothing on Android speaks it. There is no real product-facing half of this system yet.

## Users
Solo builder, 2 Android phones (this phase), 1 Wear OS watch (fast-follow, not this wave). Debug-installed via `adb install`, no Play Store / signing needed yet.

## User Stories
1. As the phone owner, my presence beacon keeps advertising when the screen is locked (foreground service, not killed by the OS within normal Doze windows).
2. As the phone owner, my beacon is authenticated — it advertises the exact 19-byte HMAC payload format `bridge.py` already verifies (Phase 6), not a plaintext name.
3. As the phone owner, my phone can also scan for other beacons and stream readings into the mesh over the existing WebSocket `reading`/`lost` message contract.
4. As a new phone, I join the realm by scanning the QR a paired node displays (Phase 6's `pairing.py` offering side) — no manual key entry.
5. As the phone owner, a settings screen shows live status: paired (yes/no), realm connection, current beacon counter, last-seen RSSI of scanned peers — so testing doesn't require reading logs.

## Acceptance Criteria
- Beacon payload bytes match the Python reference exactly for the same inputs — proven by a checked-in cross-language test vector (`key=0x11×32, uid_hash=0xDEADBEEF, counter=42` → `ae7401deadbeef0000002ad86695bb34d03d6b`), asserted in a Kotlin unit test with zero device/emulator needed.
- Foreground beacon service survives screen lock for 8+ hours on both test phones (manual real-device verification — not automatable by a build agent).
- Scanner mode receives BLE advertisements, extracts manufacturer-data payload, and forwards raw+smoothed RSSI to the existing aggregator WebSocket endpoint using the same JSON shape `messages.py` already defines (`type: "reading"` / `"lost"`) — no changes to `messages.py`/`aggregator.py` required, since the Android scanner is just another producer of the same wire format `bridge.py` already produces.
- QR pairing: app scans the QR rendered by `pairing.py`'s ASCII/offering side, completes the mutual Ed25519 exchange over the existing local TCP listener, receives realm key + version.
- Settings screen: paired status, realm key version, live RSSI table of currently-scanned peers.
- Installable via `adb install -r app-debug.apk` onto both existing phones with zero manual config beyond entering the desktop's WebSocket address once.

## Out of Scope (this phase)
Wear OS companion app (fast-follow, same `shared` module, small diff — not this wave), wake-word/assistant integration (Phase 8), Play Store release/signing, multi-user beacon identity (uid_hash remains name-derived per Phase 6 scope).
