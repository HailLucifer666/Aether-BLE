# PRD — Aether Phase 6: Security & Discovery

## Problem
Current beacon identification is a plaintext BLE advertised name match (`bridge.py` matches `adv.local_name.lower() == target_name_lower`). Anyone broadcasting that name string hijacks ownership. No node discovery (peers are static/manual). No protocol version negotiation. This blocks any public launch — it is the first thing a security-literate reader (HN, security researchers) will flag against the project.

## Users
Solo builder running the mesh across desktop, laptop, 2 Android phones, 1 Wear OS watch. Not yet multi-household — Phase 6 secures the single-realm case.

## User Stories
1. As the beacon holder, my presence advertisement cannot be replayed or spoofed by a third party recording it.
2. As a new node joining the mesh, I pair by scanning a QR code — no manual IP/key entry.
3. As an operator, if my phone reboots, the beacon resumes without a replay-vulnerable window.
4. As a protocol maintainer, I can add new message types/fields later without breaking Phase 6 nodes (version/capability handshake).
5. As a node, I discover peers on the LAN automatically (mDNS) instead of a hardcoded peer list.

## Acceptance Criteria
- Beacon payload carries `HMAC-SHA256(realm_key, uid_hash ‖ counter)`; bridge.py verifies HMAC + monotonic counter before accepting a reading.
- Replayed/tampered beacon payload → rejected, logged, never reaches `messages.py` reading/election flow.
- Counter persists to local disk per-device; survives process/OS restart without a replay window reopening at 0.
- New node pairs via QR (pubkey + mDNS service name) → mutual Ed25519 key exchange → admitted to realm.
- `hello {ver, node_id, capabilities, sig}` message type added to the wire protocol; existing `reading`/`election`/`conversation`/`ranging` messages and their consumers (dashboard) are unmodified.
- mDNS advertises `_aether._tcp.local`; a second node finds the first with zero manual config.
- Transport encryption (TLS/Noise) is explicitly deferred — PROTOCOL.md security annex documents the LAN-trust assumption for this phase.
- Key loss → manual re-pair (QR ceremony again); no recovery flow built this phase.
- All 85+ existing tests in `aether-bridge/tests/` still pass; new tests cover HMAC verify, replay rejection, counter persistence, handshake parsing.

## Out of Scope (this phase)
Transport encryption, multi-user beacons, Android app, real assistant integration, UX changes — tracked in BLUEPRINT-V2.md / Fable Build Instructions.txt Phases 7+.
