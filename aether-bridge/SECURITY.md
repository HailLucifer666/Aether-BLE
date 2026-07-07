# Security notes for aether-bridge (Phase 6)

This document is for the next engineer touching `identity.py`, `realm.py`,
`beacon_auth.py`, `pairing.py`, or `discovery.py`. See `PROTOCOL.md` §11 for
the normative wire-format spec; this file covers implementation invariants
and gotchas that aren't protocol-level concerns.

## Crypto primitives in use

| Primitive | Used for | Library |
|---|---|---|
| Ed25519 | Node identity, pairing handshake signatures | `cryptography.hazmat.primitives.asymmetric.ed25519` |
| HMAC-SHA256 (truncated to 8 bytes) | Beacon payload authentication | `hmac` + `hashlib.sha256` (stdlib) |
| SHA-256 | node_id fingerprint, uid_hash derivation | `hashlib` (stdlib) |
| `os.urandom` | Realm key generation | stdlib (CSPRNG) |

No custom crypto is implemented — every primitive above is a direct call into
`cryptography` or the stdlib. Do not hand-roll HMAC/signature verification;
always use `hmac.compare_digest` for MAC comparison (constant-time), never
`==` on raw bytes.

## Invariants that must not be broken

1. **Counter is strictly increasing, checked with `<=` not `<`.**
   `verify_beacon` rejects `counter <= last_counter`, not just `<`. An equal
   counter is a replay of the exact same payload and must be rejected
   identically to a lower one.

2. **Counter persists to disk and is never reset to zero on restart.**
   `BeaconCounterStore` loads from `~/.aether/beacon_counter.json` at
   construction. If you ever refactor this to lazy-load or cache in memory
   without loading existing state first, you reopen the replay window this
   phase was built to close. This was a confirmed product decision, not an
   implementation detail — do not "simplify" it away.

3. **HMAC uses `hmac.compare_digest`, never a manual byte comparison.**
   Manual comparison (`==` or a hand-written loop) is timing-vulnerable.
   `beacon_auth.verify_beacon` already does this correctly; preserve it in
   any future edit.

4. **The realm key never appears in logs or error messages.**
   `bridge.py` logs only `result.reason` (a short enum-like string) on
   verification failure, never the payload, the key, or the computed MAC.
   Keep it that way — a debug log that includes the realm key defeats the
   whole point of the HMAC.

5. **Grace window (`realm.py`) is small and finite.**
   `GRACE_WINDOW_VERSIONS = 3`. Do not make this unbounded — an unbounded
   grace window means a compromised old key is *never* fully retired.

6. **Private keys are stored in plaintext JSON on disk, protected only by OS
   filesystem permissions.** There is no passphrase or OS-keychain
   integration this phase. This is consistent with the LAN-trust posture
   documented in `PROTOCOL.md` §11.4 — if this project ever crosses a trust
   boundary (multi-user machine, cloud deployment), this needs to change
   before that happens, not after.

7. **`AETHER_MFG_ID = 0xFFFF` in `bridge.py` is the Bluetooth SIG's reserved
   test/internal-use Company Identifier**, not a registered ID for this
   project. If Aether ever becomes a registered SIG member, this should be
   replaced with an assigned Company ID — but there's no urgency; 0xFFFF is
   valid for indefinite internal/hobbyist use per the SIG's own allocation
   rules.

## What is explicitly NOT protected against (by design, this phase)

- **Network-level eavesdropping/tampering of WebSocket traffic** (scanner↔
  aggregator, aggregator↔client). Only the BLE beacon advertisement channel
  is authenticated this phase. See `PROTOCOL.md` §11.4.
- **A malicious node that has completed pairing.** Pairing establishes trust
  once; there's no ongoing revocation mechanism beyond realm key rotation
  (which requires re-pairing every member, since there's no revocation-only
  primitive).
- **Physical access to a node's disk.** Identity/realm files are plaintext on
  disk; anyone with filesystem access to a paired device can extract the
  keys.
- **Multi-user beacon disambiguation.** `uid_hash` exists in the wire format
  but `election.py` (untouched this phase) has no concept of multiple
  simultaneous beacon owners.

## Key-loss recovery

There is no backup, export, or recovery flow. If `~/.aether/identity.json`
or `~/.aether/realm.json` is lost or corrupted, the only path back into the
mesh is re-running the QR pairing ceremony (`pairing.py`) from scratch. This
was a confirmed product decision for this phase — do not build a recovery
flow without checking whether that decision has changed.
