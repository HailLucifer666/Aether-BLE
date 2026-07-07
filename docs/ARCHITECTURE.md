# Architecture — Phase 6: Security & Discovery

## Components (new, all under `aether-bridge/`)

- **`identity.py`** — Ed25519 keypair generation/load (`~/.aether/identity.json`), node_id derivation (pubkey fingerprint).
- **`realm.py`** — realm key storage/versioning (`~/.aether/realm.json`: `{realm_key_v: int, realm_key: hex, members: [node_id...]}`), grace-window verification against last N key versions.
- **`beacon_auth.py`** — build/verify the authenticated beacon payload:
  `[2B magic | 1B ver | 4B uid_hash | 4B counter | 8B HMAC-SHA256(realm_key, uid_hash‖counter)]` (19 bytes, fits in BLE manufacturer-data). Counter persisted to `~/.aether/beacon_counter.json`, loaded at startup — never restarts at 0 on reboot (per confirmed decision: persist-to-disk).
- **`pairing.py`** — QR pairing ceremony: generates a payload `{pubkey, mdns_name, realm_invite_token}`, renders via `qrcode`, mutual key exchange handshake over a short-lived local TCP/WS listener.
- **`discovery.py`** — `zeroconf` service registration/browsing for `_aether._tcp.local`.
- **`messages.py` (extend, don't break)** — add `build_hello_message(ver, node_id, capabilities, sig)`; existing `build_reading_message`/`build_election_message`/etc. untouched (additive per the file's own documented contract).
- **`bridge.py` (extend)** — replace name-string matching (`_raw_name` / `target_name_lower` check) with: parse `adv.manufacturer_data`, call `beacon_auth.verify_beacon(payload, realm_key, last_counter)`; on success proceed with existing RSSI/EMA/reading flow unchanged; on failure/replay, drop silently (log at debug level, don't spam terminal).

## Data Flow (new beacon verification path)

```
BLE advertisement → bridge.py._on_advertisement()
  → extract manufacturer_data[AETHER_MFG_ID]
  → beacon_auth.verify_beacon(payload, realm_key, last_counter[uid_hash])
      - counter <= last_counter → reject (replay)
      - HMAC mismatch → reject (spoof/corruption)
      - else → return uid_hash, update last_counter[uid_hash], persist to disk
  → existing EMA smoothing / BeaconState / build_reading_message (UNCHANGED)
```

## Protocol Changes (PROTOCOL.md v2 annex, additive)
- New message type `hello` (bidirectional, sent on connect before any other message).
- New security annex section: beacon payload format, realm key rotation, explicit statement that transport (WebSocket) remains unencrypted/LAN-trust in this phase — deferred, not forgotten.
- No changes to `reading`/`election`/`conversation`/`ranging` schemas — `election.py`, `conversation.py`, `ranging.py`, `aggregator.py` are NOT touched this phase.

## What is explicitly NOT built here
TLS/Noise transport, Android app, multi-user beacon (uid_hash is single-user for now), key-loss recovery flow (manual re-pair only, per confirmed decision).
