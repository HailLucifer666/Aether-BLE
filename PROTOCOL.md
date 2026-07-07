# Aether Protocol Specification

**Version 1.0 · Status: Stable (describes the implementation as of commit `3eaa52b`)**

This document specifies the Aether Protocol independently of this repository's
Python/TypeScript implementation, so that a compatible scanner, aggregator, or
client can be built in any language without reading the source code. Where a
rule below is ambiguous, the reference implementation in `aether-bridge/` is
authoritative.

## 1. Scope

Aether solves exactly one problem: **given N devices capable of sensing a
person's proximity, decide which single device should own an interaction with
that person at any given moment, and hand ownership off smoothly as the person
moves.** It does not specify wake-word detection, speech recognition, dialogue
generation, or any device's actual response behavior — those are the
responsibility of whatever system sits on top of an Aether node. Aether tells
you *who should answer*; it does not answer.

## 2. Actors

| Actor | Role |
|---|---|
| **Scanner** | Senses proximity to a tracked beacon (e.g. BLE RSSI to a phone) and serves its own readings over a WebSocket. A scanner has no knowledge of any other scanner. |
| **Aggregator** | Connects out to every configured scanner as a WebSocket *client*, fuses their readings, runs the election, and serves the result over its own WebSocket *server*. Exactly one aggregator per mesh. |
| **Client** (dashboard, or any consumer) | Connects to the aggregator's WebSocket as a client. Renders the current state; may send `wake` and `say` requests. A client makes no decisions — it displays what the aggregator already decided. |

Topology is a star, not a mesh in the graph-theory sense: scanners never talk
to each other or to clients directly, only to the aggregator.

```
Scanner A ─┐
Scanner B ─┼──(aggregator dials out as WS client)──▶ Aggregator ──(serves WS)──▶ Client(s)
Scanner N ─┘
```

## 3. Transport

All messages are UTF-8 JSON text frames over WebSocket (RFC 6455). Every
message is a JSON object with a top-level `"type"` field; unrecognized message
types or malformed JSON MUST be silently ignored by the receiver, not treated
as fatal — this is what allows new message types to be added without breaking
older peers (see §8, Versioning).

Timestamps in the `"ts"` field are `HH:MM:SS` local time, for human display
only — do not use them for ordering or timing math. Use the monotonic `tick`
counter (§5.2) or the caller's own clock for that.

## 4. Scanner → Aggregator contract

A scanner MUST run its own WebSocket server and, for every connected client
(normally just the aggregator), send its current state immediately on connect,
then again on every subsequent update. A scanner MUST NOT wait for a request —
it pushes.

### 4.1 `reading` — a live proximity observation

```json
{
  "type": "reading",
  "scanner": "Scanner-A",
  "name": "OnePlus 7T",
  "rssi": -58.2,
  "smoothedRssi": -59.1,
  "lastSeenMs": 340,
  "ts": "14:32:41"
}
```

| Field | Type | Meaning |
|---|---|---|
| `scanner` | string | This scanner's stable identifier. Used as the election candidate id. |
| `name` | string | The tracked beacon's advertised name (informational). |
| `rssi` | number | Latest raw signal-strength reading, in dBm. |
| `smoothedRssi` | number | `rssi` after smoothing (reference implementation: EMA, α=0.3 — `smoothed = α·raw + (1-α)·prev`, first sample seeds the average). Smoothing algorithm is an implementation detail; only the output value is part of the contract. |
| `lastSeenMs` | integer ≥ 0 | Milliseconds since the beacon was last actually observed. |

### 4.2 `lost` — the scanner cannot currently see the beacon

```json
{"type": "lost", "scanner": "Scanner-A", "name": "OnePlus 7T", "ts": "14:32:41"}
```

Sent instead of `reading` once the beacon has been unseen past the scanner's
own lost-detection threshold. A scanner reporting `lost` is treated by the
aggregator as **not present** (§5.3) — it cannot be the election owner, but it
remains a configured peer and will be reconsidered the moment it resumes
sending `reading`.

### 4.3 Timing

The reference implementation broadcasts on a fixed interval independent of new
data (400ms) and additionally applies a stall watchdog that force-restarts
scanning if no observation of *any* kind arrives for `lost_threshold +
grace_period`. Exact intervals are an implementation choice; a conformant
scanner MUST broadcast at a bounded, roughly-regular interval so the
aggregator's per-scanner `lastSeenMs` stays meaningful, but the protocol does
not mandate a specific number.

## 5. Election algorithm

This section is normative — the exact numbers below are the interoperability
contract, not tuning suggestions. A challenger implementation MUST reproduce
this decision procedure exactly, or election outcomes will disagree with the
reference aggregator and any dashboard mixing the two.

### 5.1 Constants

| Constant | Value |
|---|---|
| `HYSTERESIS_DB` | 5.0 |
| `HYSTERESIS_CONSECUTIVE` | 2 |
| `CONTEST_MARGIN_DB` (tier-2 escalation only, §7) | 3.0 |

### 5.2 Candidacy

A scanner is a **candidate** on a given tick iff it is `present` (has sent
`reading`, not `lost`, within the aggregator's liveness window) AND has a
non-null smoothed RSSI. Absent scanners are never candidates and can never be
elected, even if they were the previous owner.

Each scanner MAY be configured with an additive **calibration offset** in dB,
applied before any comparison: `calibrated_rssi = smoothed_rssi +
calibration_offset`. This exists so scanners with different radio/antenna
sensitivity can be normalized onto a common scale — without it, a
systematically over-reporting radio could steal ownership from a scanner that
is truly closer. All election comparisons in this section use
`calibrated_rssi`, never raw `smoothedRssi`.

### 5.3 The `elect()` decision procedure

Run once per tick, given: the current owner (or null), the full candidate
list, and a `challenger` streak-tracker carried from the previous tick.

1. **No candidates** → owner becomes `null`. Challenger streak resets.
2. **Owner is null, or the current owner is not a candidate this tick**
   (first contact, or the owner just went absent) → the strongest candidate
   wins **immediately** — no hysteresis delay on re-acquisition. Challenger
   streak resets.
3. **Owner is a candidate and no other candidates exist** → owner keeps
   ownership trivially. Challenger streak resets.
4. **Otherwise**, compare the incumbent against the strongest other candidate
   (the "challenger"):
   - If `challenger.calibrated_rssi - incumbent.calibrated_rssi <
     HYSTERESIS_DB` → the incumbent keeps ownership. Any in-progress
     challenge streak resets to zero, **even if the same challenger is still
     ahead** — the margin must be re-cleared every tick, not just once.
   - Otherwise the challenger currently exceeds the hysteresis margin:
     - If this is the *same* challenger id as last tick's in-progress streak,
       increment the streak; otherwise reset the streak to 1 (a different
       scanner becoming the strongest challenger restarts the count).
     - If the streak has now reached `HYSTERESIS_CONSECUTIVE`, ownership
       transfers to the challenger this tick (a handoff event is emitted —
       see §6.1). The streak resets to zero.
     - Otherwise ownership is unchanged this tick, but the streak is carried
       forward for the next tick.

**Tie-break:** whenever "the strongest candidate" or "the strongest other
candidate" must be chosen and two or more are tied on `calibrated_rssi`
exactly, the one with the lexically smaller id wins. This same rule applies
uniformly in tier-2 ranging (§7) so the two tiers never disagree on tie
policy.

**Rationale for re-checking the margin every tick** rather than treating a
single margin-crossing as sufficient: RSSI is noisy. Requiring the challenger
to *hold* a clear lead for `HYSTERESIS_CONSECUTIVE` consecutive ticks — not
merely have crossed it once at some point — is what prevents a momentary noise
spike from starting a handoff that noise then immediately reverses.

## 6. Aggregator → Client contract

The aggregator broadcasts on its own tick interval (reference: 400ms) to every
connected client. A newly-connecting client MUST receive the current full
state immediately, not wait for the next scheduled tick.

### 6.1 `election` — the current ownership decision (mandatory)

```json
{
  "type": "election",
  "owner": "Scanner-A",
  "tick": 4831,
  "ts": "14:32:41",
  "scanners": [
    {"id": "Scanner-A", "rssi": -58.2, "smoothedRssi": -59.1, "lastSeenMs": 340, "present": true},
    {"id": "Scanner-B", "rssi": null, "smoothedRssi": null, "lastSeenMs": null, "present": false}
  ],
  "lastHandoff": {"from": "Scanner-B", "to": "Scanner-A", "atTick": 4821, "ts": "14:32:15"},
  "wakeOutcome": {
    "requestedAtTick": 4830, "ts": "14:32:41", "owner": "Scanner-A",
    "results": [
      {"id": "Scanner-A", "outcome": "ACCEPTED"},
      {"id": "Scanner-B", "outcome": "SUPPRESSED"}
    ]
  }
}
```

Rules:
- `owner` is `null` when no candidate is present.
- `scanners` MUST list every configured peer, in stable order, on every
  broadcast — including absent ones with `present: false` and all numeric
  fields `null`. Clients rely on a fixed-length, fixed-order list; never omit
  an absent scanner.
- `lastHandoff` is `null` until the first handoff ever occurs, then holds the
  most recent one. It is not one-shot — a client reconnecting mid-session
  still needs to know the last handoff, so it persists across broadcasts
  until superseded by a newer one. Clients wanting a full handoff history
  must accumulate this field themselves.
- `wakeOutcome` is **one-shot**: it appears on exactly the one broadcast
  immediately following a `wake` request (§6.4) and is `null` on every other
  broadcast. `outcome` is `"ACCEPTED"` for the current owner and
  `"SUPPRESSED"` for every other scanner listed in `results`.

### 6.2 `conversation` — portable utterance state (optional extension)

Sent as an independent message (not merged into `election`) immediately after
it, only when there is something to report (an active utterance, non-empty
transcript, or a pending `conversationEvent`).

```json
{
  "type": "conversation",
  "transcript": [
    {"id": 1, "scanner": "Scanner-A", "role": "assistant", "text": "...", "ts": "14:32:41"}
  ],
  "utterance": {
    "text": "...", "audioBase64": "data:audio/mp3;base64,...",
    "durationMs": 2400, "offsetMs": 0, "isSynthetic": false
  },
  "speakingScanner": "Scanner-A",
  "phase": "IDLE",
  "phaseFrom": null,
  "phaseTo": null,
  "conversationEvent": null
}
```

`phase` is one of `IDLE | PREPARE | TRANSFER | CONFIRM | RELEASE` (see §6.3).
`utterance` and `speakingScanner` are `null` when nothing is active.
`conversationEvent` is one-shot, mirroring `wakeOutcome`'s semantics — present
only on the broadcast marking a phase transition, `null` otherwise.

### 6.3 Conversation handoff FSM

When the elected owner changes while an utterance is actively "speaking" on
the losing scanner, ownership of that utterance migrates through a four-phase
sequence rather than cutting off abruptly:

```
IDLE → PREPARE → TRANSFER → CONFIRM → RELEASE → IDLE
```

| Phase | Duration | What happens |
|---|---|---|
| `PREPARE` | 200ms | New owner is notified to ready itself. Speaker attribution is still the OLD owner. |
| `TRANSFER` | 200ms | Playback pauses; the current playhead offset is recorded. Speaker attribution still OLD owner. |
| `CONFIRM` | 200ms | Speaker attribution flips to the NEW owner. Playback seeks to the recorded offset but does not yet resume. |
| `RELEASE` | 200ms | Playback resumes from the recorded offset, now attributed to the NEW owner. |

A handoff only starts this FSM if an utterance is currently active AND the
scanner losing ownership is the one currently speaking it — a handoff with no
active utterance, or where the losing scanner wasn't speaking, is a no-op.
Starting a brand-new utterance while a handoff is mid-flight cancels the
handoff (the new utterance takes over cleanly under `IDLE`).

### 6.4 Client → Aggregator inbound messages

**`wake`** — request a wake-word arbitration outcome:

```json
{"type": "wake", "requestId": "a1b2c3"}
```

`requestId` is optional and may be omitted or ignored by the aggregator (it
exists for a caller wanting to correlate its own request with the resulting
`wakeOutcome`, not for any protocol-level matching). On receipt, the current
owner is marked `ACCEPTED` and every other present-or-configured scanner is
marked `SUPPRESSED` in the next `election` broadcast's `wakeOutcome`.

**`say`** — request the current owner to speak text:

```json
{"type": "say", "text": "Hello, I am Aether"}
```

Empty or non-string `text` MUST be ignored. A valid request starts a new
utterance (§6.3) attributed to the current owner. Malformed or unrecognized
inbound message types MUST be ignored, not disconnect the client (§3).

## 7. Tier-2 ranging (BLE + near-ultrasound fusion)

BLE signal strength alone cannot distinguish "N meters away in the same room"
from "N meters away through a wall" — the physical signal looks identical.
Tier 2 exists to resolve exactly the elections where this ambiguity would
otherwise decide ownership incorrectly.

### 7.1 Contest detection

On every tick, given the current `owner` and full candidate list, a
**contest** exists iff there is a present incumbent and a present challenger
whose gap satisfies:

```
-CONTEST_MARGIN_DB ≤ (challenger.calibrated_rssi - incumbent.calibrated_rssi) < HYSTERESIS_DB
```

i.e. the challenger is close enough that tier 1 cannot confidently call it,
but has not yet cleared the full hysteresis margin. `CONTEST_MARGIN_DB` (3.0)
is intentionally tighter than `HYSTERESIS_DB` (5.0) so that only genuine
photo-finishes escalate — not every routine challenge.

### 7.2 Chirp measurement

On a contest, the challenger device emits a near-ultrasound chirp (reference
range: 18–21kHz, 50–100ms — inaudible, and does not pass through walls). Each
scanner that hears it reports a one-way time-of-flight in microseconds,
converted to distance via `distance_m = (tof_us / 1_000_000) × 343.0` (speed
of sound in dry air at ~20°C). A scanner that does NOT hear the chirp
(different room, out of range, mic busy) simply has no measurement — that
**absence is itself the room-containment signal**, not an error condition.

The chirp's winner is the reporting scanner with the smallest `distanceM`
(ties broken by lexically smaller id, per §5.3). `sameRoom` is `true` iff
*both* contest parties (incumbent and challenger) produced a measurement.

### 7.3 Fusion precedence

Given the BLE-elected owner, the active contest, and the chirp result, the
resolved owner is decided by this precedence ladder (highest to lowest
authority is read top to bottom in terms of "what actually happened", not
priority order — there is exactly one applicable rule per tick):

| `fusionReason` | Condition | Result |
|---|---|---|
| `ble-only` | No contest, or no chirp yet, or chirp heard nothing | BLE owner stands unchanged |
| `chirp-confirmed` | Chirp winner IS the current BLE owner | BLE owner stands, now corroborated |
| `chirp-resolved-tie` | Chirp winner is the other contest party, is currently present, AND the BLE owner also heard the chirp | Ownership overridden to the chirp winner |
| `chirp-room-containment` | Chirp winner is the other contest party, is currently present, AND the BLE owner did **not** hear the chirp | Ownership overridden to the chirp winner |

A chirp winner that is not currently a present candidate MUST NOT be honored
— tier 2 must never hand ownership to a device tier 1 considers absent; the
result falls back to `ble-only` in that case.

### 7.4 `ranging` broadcast message (additive extension)

```json
{
  "type": "ranging",
  "contest": {
    "incumbentId": "Scanner-A", "challengerId": "Scanner-B",
    "incumbentRssi": -60.0, "challengerRssi": -58.0, "atTick": 4830
  },
  "chirp": {
    "measurements": [
      {"scannerId": "Scanner-A", "tofUs": 2000.0, "distanceM": 0.686},
      {"scannerId": "Scanner-B", "tofUs": 1000.0, "distanceM": 0.343}
    ],
    "winnerId": "Scanner-B", "sameRoom": true, "resolvedTick": 4831
  },
  "fusionReason": "chirp-resolved-tie",
  "rangingEvent": {
    "phase": "CHIRP", "contestIncumbent": "Scanner-A", "contestChallenger": "Scanner-B",
    "winnerId": "Scanner-B", "sameRoom": true, "atTick": 4831
  }
}
```

`contest` and `chirp` are `null` when not applicable. `rangingEvent` is
one-shot (§6.1 semantics) marking a chirp round for a client to react to (e.g.
a visual "ping" animation). This message type is purely additive — a client
unaware of `"type": "ranging"` continues to function correctly on `election`
and `conversation` alone (§3).

## 8. Versioning and compatibility

Prior to Phase 6, Aether had no version-negotiation handshake. Compatibility
is maintained by a strict rule: **new message types and new optional fields
may be added at any time; existing field names, types, and semantics defined
in this document MUST NOT change.** A conformant implementation ignores
unrecognized `"type"` values and unrecognized fields on known types (§3) —
this is what makes the tier-2 ranging extension (§7) safe to add without
breaking a client built against §5–6 alone, and is the same discipline any
future extension must follow.

### 8.1 `hello` — version and capability handshake (Phase 6 addition)

Sent bidirectionally, once, immediately after a connection is established and
before any other message type. Purely additive per the rule above — a peer
that does not understand `"type": "hello"` simply ignores it and continues
functioning on the message types it does understand.

```json
{
  "type": "hello",
  "ver": 1,
  "node_id": "a1b2c3d4e5f6a7b8",
  "capabilities": ["beacon", "ranging"],
  "sig": "3045022100...",
  "ts": "14:32:41"
}
```

| Field | Type | Meaning |
|---|---|---|
| `ver` | integer | Protocol version this node speaks. |
| `node_id` | string | Stable fingerprint of the sender's Ed25519 public key (see Security Annex, §11.1). |
| `capabilities` | array of string | Feature tags the sender supports (e.g. `"beacon"`, `"ranging"`), allowing a receiver to adapt without a version number alone. |
| `sig` | string (hex) | Ed25519 signature over the handshake payload, authenticating `node_id`'s claimed identity. |

`hello` establishes identity and capabilities; it does not itself gate
whether `reading`/`election`/`conversation`/`ranging` messages are accepted —
those contracts are unchanged by this addition.

## 9. Reference implementation map

| Section | Implemented in |
|---|---|
| §4 Scanner contract | `aether-bridge/bridge.py` (real BLE), `aether-bridge/simulated_scanner.py` (synthetic) |
| §5 Election | `aether-bridge/election.py` |
| §6 Aggregator/client contract | `aether-bridge/aggregator.py`, `aether-bridge/messages.py` |
| §6.3 Conversation FSM | `aether-bridge/conversation.py` |
| §7 Tier-2 ranging | `aether-bridge/ranging.py` |
| §8.1 `hello` handshake | `aether-bridge/messages.py`, `aether-bridge/identity.py` |
| §11 Security Annex | `aether-bridge/beacon_auth.py`, `aether-bridge/realm.py`, `aether-bridge/identity.py`, `aether-bridge/pairing.py`, `aether-bridge/discovery.py` |
| Client rendering | `aether-dashboard/src/app/mesh/` |

## 10. Non-goals (explicitly out of scope)

- Wake-word detection, speech-to-text, dialogue generation, and text-to-speech
  are not specified here — Aether decides *who* should handle an interaction,
  not *how* it's handled.
- Transport encryption (TLS/Noise) between scanners, aggregator, and clients
  is not specified — see §11.4. The reference implementation assumes a
  trusted local network; production deployments crossing a trust boundary
  need to add this independently.
- Multi-user beacon ownership is not specified — the `uid_hash` field in the
  beacon payload (§11.2) exists for future use, but election/ownership logic
  (§5) remains single-user.
- Key-loss recovery is not specified — a lost or corrupted identity/realm key
  requires re-running the pairing ceremony (§11.3); no backup/export flow is
  part of this protocol.

## 11. Security Annex (Phase 6)

This section specifies the beacon-authentication and node-identity mechanisms
introduced in Phase 6. It does not change any message schema defined in §4–7.

### 11.1 Node identity

Each node generates an Ed25519 keypair on first run and persists it locally
(reference implementation: `~/.aether/identity.json`). A node's `node_id` is
a fingerprint of its public key: `sha256(pubkey)`, hex-encoded, truncated to
16 hex characters (64 bits). This is used in the `hello` handshake (§8.1) and
in mDNS TXT records (§11.5).

### 11.2 Authenticated beacon payload

To prevent a plaintext advertised-name match from being trivially spoofed or
replayed, the beacon advertises a fixed 19-byte authenticated payload instead
of relying solely on its advertised name, carried in the BLE manufacturer-data
field:

```
[2B magic | 1B ver | 4B uid_hash | 4B counter | 8B HMAC-SHA256(realm_key, uid_hash || counter)]
```

| Field | Size | Meaning |
|---|---|---|
| `magic` | 2 bytes | Fixed marker identifying this as an Aether beacon payload. |
| `ver` | 1 byte | Payload format version. |
| `uid_hash` | 4 bytes, big-endian | Identifies the beacon (single-user this phase — see §10). |
| `counter` | 4 bytes, big-endian | Strictly increasing per-beacon nonce. |
| `mac` | 8 bytes | First 8 bytes of `HMAC-SHA256(realm_key, uid_hash‖counter)` — truncated to fit the BLE manufacturer-data budget. |

A receiver (scanner) MUST reject a payload if any of the following hold:

- The payload is not exactly 19 bytes, or the magic/version fields don't match
  a supported value.
- `counter <= last_accepted_counter` for that `uid_hash` (replay or stale —
  counters are strictly increasing, equal counters are rejected, not just
  lower ones).
- The received MAC does not match `HMAC-SHA256(realm_key, uid_hash‖counter)`
  recomputed locally (spoofed or corrupted payload).

A rejected payload MUST be dropped silently (no propagation into the
`reading`/`election` flow) and SHOULD be logged at debug level only — a
malicious or malfunctioning nearby device broadcasting garbage on this
channel is expected background noise, not something that should spam an
operator's terminal.

**Counter persistence:** the last-accepted counter per `uid_hash` MUST be
persisted to local disk (reference implementation:
`~/.aether/beacon_counter.json`) and reloaded on startup. A process or device
restart MUST NOT reset the counter to zero — doing so would reopen a replay
window for every payload broadcast before the restart.

### 11.3 Realm key, versioning, and rotation

A "realm" is the shared secret admitting nodes to one mesh. The realm key is
a 256-bit value used as the HMAC key in §11.2. It is versioned
(`realm_key_v`, an incrementing integer) so it can be rotated without a
mesh-wide simultaneous update: verification is accepted against any of the
last N key versions (reference implementation: N=3) — a "grace window" that
tolerates a beacon or scanner briefly running the previous key version across
a rotation. Versions older than the grace window are pruned and no longer
accepted.

New nodes are admitted to a realm via the QR pairing ceremony (§11.4), not by
any in-band protocol message — realm membership is an out-of-band trust
decision, not something a peer can request over the wire.

### 11.4 Explicit LAN-trust assumption (transport encryption deferred)

**Transport encryption (TLS/Noise) is explicitly deferred, not forgotten.**
All WebSocket traffic in §3–7 (scanner↔aggregator, aggregator↔client) remains
plaintext in this phase, exactly as before Phase 6. The mechanisms in this
annex authenticate and replay-protect the *beacon* advertisement channel
specifically; they do not encrypt or authenticate the WebSocket transport.

This is a deliberate, scoped decision for a solo-operator single-realm
deployment on a trusted home LAN — not a claim that the protocol is secure
against a network-level attacker. Any deployment crossing a trust boundary
(untrusted network, multi-tenant environment, internet-facing) MUST add
transport encryption before relying on this protocol; that hardening is
tracked for a later phase and is out of scope here.

### 11.5 Pairing and discovery (informative — not wire-protocol-normative)

New nodes join a realm via a QR-code ceremony: the admitting node displays a
QR encoding `{pubkey, mdns_name, realm_invite_token}`; the joining device
scans it and performs a mutual Ed25519 key exchange over a short-lived local
listener. This ceremony is a local, out-of-band bootstrap step, not a
WebSocket message type, so it has no wire-format entry in §3–8.

Peer discovery uses mDNS (`_aether._tcp.local`) in place of a static,
operator-supplied peer list. This replaces the peer-list mechanism referenced
in §10 of prior versions of this document; it is a discovery mechanism, not a
protocol message, and does not change §4–7.

**Key loss:** if a node's identity or realm key is lost or corrupted, the
only recovery is re-running the pairing ceremony. No backup/export flow is
provided or specified.
