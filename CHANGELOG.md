# Changelog

## Phase 1

- **Real BLE bridge implemented** — continuous scanner via `bleak` matches target beacon by advertised local name, smooths RSSI with EMA (alpha=0.3), and broadcasts state over WebSocket on `ws://127.0.0.1:8765`.
- **Diagnostic gate (`diag.py`)** — separates radio/driver/permissions issues from beacon name-match failures, providing unambiguous failure modes for troubleshooting.
- **Live BLE dashboard mode** — dashboard now connects to the real bridge, displays connection status, real-time RSSI sparkline, and ownership state driven by actual beacon proximity.
- **Beacon lost detection** — watchdog fires after 3 seconds of no advertisement; clears immediately on next advertisement (accounts for phone screen lock behavior).
- **Architecture fix: ownership release** — with only one real device, arbitration can now reach "no owner" state when beacon is absent (previously impossible in simulation-only phase).

**Status:** Code complete, build verified. Awaiting hardware verification (requires human with OnePlus 7T running nRF Connect advertising as `AetherUser1`).
