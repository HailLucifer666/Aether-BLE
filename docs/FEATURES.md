# Features — Phase 6 (MVP = all of these; no stretch goals this phase)

1. **[MVP] Authenticated rotating beacon** — HMAC+counter payload, replay-proof, persisted counter.
2. **[MVP] Realm key + versioning** — shared secret admitting nodes to "home realm"; version-tagged for future rotation.
3. **[MVP] QR pairing ceremony** — new node admitted via QR scan + Ed25519 mutual key exchange (server side generates/displays QR; scanning side is stubbed/CLI for now since the Android app is Phase 7).
4. **[MVP] mDNS discovery** — `_aether._tcp.local` registration/browsing, replaces static peer list.
5. **[MVP] `hello` handshake** — version + capability negotiation added to PROTOCOL.md v2, wired into `bridge.py`'s connection flow.
6. **[Deferred] Transport encryption (TLS/Noise)** — documented as LAN-trust assumption in this phase, built in a later hardening phase.
7. **[Deferred] Key-loss recovery** — manual re-pair only; no backup/export flow.
8. **[Deferred] Multi-user beacons** — uid_hash exists in the payload format but the election/ownership logic remains single-user this phase (Phase 6 doesn't touch `election.py`).
