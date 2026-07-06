# Changelog

## Phase 1

- **Real BLE bridge implemented** — continuous scanner via `bleak` matches target beacon by advertised local name, smooths RSSI with EMA (alpha=0.3), and broadcasts state over WebSocket on `ws://127.0.0.1:8765`.
- **Diagnostic gate (`diag.py`)** — separates radio/driver/permissions issues from beacon name-match failures, providing unambiguous failure modes for troubleshooting.
- **Live BLE dashboard mode** — dashboard now connects to the real bridge, displays connection status, real-time RSSI sparkline, and ownership state driven by actual beacon proximity.
- **Beacon lost detection** — watchdog fires after 3 seconds of no advertisement; clears immediately on next advertisement (accounts for phone screen lock behavior).
- **Architecture fix: ownership release** — with only one real device, arbitration can now reach "no owner" state when beacon is absent (previously impossible in simulation-only phase).

**Status:** Code complete, build verified. Awaiting hardware verification (requires human with OnePlus 7T running nRF Connect advertising as `AetherUser1`).

## Phase 2 (build 2) — Multi-device mesh + leader election

- **Mesh aggregator (`aggregator.py`)** — connects out as a WebSocket client to each peer scanner (real `bridge.py` or `simulated_scanner.py`), fuses readings, runs `election.py` on a 400 ms tick, and serves its own endpoint at `ws://127.0.0.1:8766` broadcasting the election state. Hub-and-spoke fusion, not BLE Mesh flooding.
- **Pure leader-election logic (`election.py`)** — zero I/O, fully unit-testable. A challenger must beat the incumbent's smoothed RSSI by **5 dBm for 2 consecutive ticks** before ownership transfers. Exact ties broken by lexically smaller scanner id. Per-scanner `calibration_offset` cancels radio miscalibration.
- **Wake resolution** — a wake trigger (terminal spacebar/enter or inbound WS `{type:"wake"}`) resolves to `ACCEPTED` for the current owner and `SUPPRESSED` for all others; attached as a one-shot `wakeOutcome` to exactly one broadcast.
- **Headless simulated scanner (`simulated_scanner.py`)** — same WS contract as `bridge.py`, synthesizes RSSI (optionally scripted to ramp through segments), so the aggregator and election logic can be demoed end-to-end with no BLE radio.
- **Calibration wiring** — per-scanner offsets passed inline on the aggregator's `--peers` flag (`ws://host:port=offset`); applied additively during election. The "kill-test" cases in `tests/test_election.py` prove ownership is *wrong* without this correction when one radio over-reports.
- **Mesh dashboard mode** — new `src/app/mesh/` viewer (owner spotlight, ranked per-scanner signal list, server-driven handoff log, Wake button). Strictly read-only; never arbitrates ownership locally.
- **Tests** — 24 pytest tests green: 12 covering the pure election logic (incl. the two cross-source kill-tests proving stability-without-correctness and correctness-with-offset), plus 12 new end-to-end tests driving a real aggregator against in-process mock peer servers (`tests/test_aggregator.py`).
- **One-click mesh demo** — new `AetherMesh.bat` launches two simulated scanners + aggregator + dashboard in split panes; toggle Source → Mesh to watch an ownership handoff within ~30 s.
- **Docs synced** — README, CHANGELOG, and bridge README now reflect Phase 2; closed the prior "Phase 2 not started" doc/code drift.

**Status:** Code complete, 24/24 tests green, dashboard build clean. Hardware verification of multi-scanner mesh deferred (requires ≥2 physical scanning machines); the simulated mesh is fully demonstrable via `AetherMesh.bat`.

## Phase 3 (build 1) — Portable conversation state

The conversation becomes a portable object that migrates between scanners on ownership handoff. Type a message and the current owner speaks it aloud via real `edge-tts` neural TTS (Microsoft neural voices, **free, no API key**); when ownership changes mid-sentence, a four-phase contract migrates the utterance — the assistant literally finishes its sentence on the next device.

- **Pure conversation FSM (`conversation.py`)** — zero I/O, fully unit-testable, mirrors `election.py`'s discipline. The `PREPARE → TRANSFER → CONFIRM → RELEASE` handoff sequence, utterance lifecycle, and progress tracking (frozen during TRANSFER so the broadcast doesn't advance while audio is paused).
- **Aggregator integration (`aggregator.py`)** — handoff detection in the election tick loop kicks off the FSM when ownership changes during an active utterance; a new `_conversation_fsm_loop` background task drives phase transitions and finishes utterances when their duration elapses.
- **`say` inbound + edge-tts generation** — a new `{"type":"say","text"}` message generates an mp3 via `edge-tts` (lazy import, so the aggregator runs without it) and assigns it to the current owner. On ANY failure (module missing, network down, service error) the system falls back to a synthetic utterance with the same FSM/broadcast semantics but no audio — the migration demo still works offline.
- **New wire messages** — `{"type":"conversation",...}` broadcast (transcript, utterance with audioBase64/durationMs/isSynthetic, speakingScanner, phase, phaseFrom/To, one-shot conversationEvent) and `{"type":"say"}` outbound. Leaves the locked `ElectionMessage` completely untouched.
- **Dashboard conversation UI (`src/app/mesh/`)** — new Conversation panel: transcript list, "Say something" input, a 4-step PREPARE→TRANSFER→CONFIRM→RELEASE phase pill that lights up during migration, an animated speaking-wave indicator on the active scanner's card, and a hidden `<audio>` element driven by the hook. On TRANSFER the audio pauses at the current word; on RELEASE it seeks back to that offset and resumes — the audible "the sentence moved" moment.
- **Graceful offline path** — when `edge-tts` is unavailable the dashboard shows a "TTS offline — simulating" badge and the conversation still migrates visually through all four phases.
- **Tests** — 50 pytest tests green: 12 election, 19 pure conversation FSM, 19 aggregator end-to-end (incl. synthetic fallback when edge-tts is missing, full handoff FSM through all four phases via the broadcast loop, conversation-event one-shot semantics, and conversation-message suppression before the first say).
- **Dependency** — `edge-tts>=6.0` added to `requirements.txt`.

**Status:** Code complete, 50/50 tests green, dashboard build clean. End-to-end demo: `AetherMesh.bat` → Source → Mesh → type a sentence → hear it speak → wait ~15 s for the simulated handoff → hear the sentence pause ~400 ms during TRANSFER/CONFIRM and resume from the same word under the new owner.

**Non-goals deferred to a later build:** real LLM/intent understanding (user types literal text), real per-scanner audio output (single laptop speaker is "the room"; browser-played and visually attributed), aggregator→peer command channel (peers stay passive; FSM runs centrally).

## Phase 4 (build 1) — Tiered sensing: BLE + near-ultrasound chirp fusion

BLE alone cannot distinguish same-room from behind-a-wall (2 m in-room and 2 m through drywall produce identical RSSI). Phase 4 adds a second sensing tier: a near-ultrasound chirp (18–21 kHz, 50–100 ms) fired on demand when the BLE election is contested, producing time-of-flight distance measurements and a boolean same-room proof that BLE fundamentally cannot provide.

- **Pure ranging module (`ranging.py`)** — zero I/O, fully unit-testable, mirrors `election.py`/`conversation.py` discipline. `detect_contest()` identifies contested elections (challenger within 3.0 dB margin but not exceeding hysteresis). `tof_to_distance()` converts round-trip time to meters (`SPEED_OF_SOUND_M_S = 343.0`). `chirp_from_measurements()` picks the winner and computes same-room presence (did both contest parties hear the chirp?). `fuse()` applies the precedence ladder: `ble-only` → `chirp-confirmed` → `chirp-resolved-tie` → `chirp-room-containment` (highest authority — overrides BLE's wrong answer when the winner didn't hear the chirp because it's behind a wall).
- **Ranging source seam (`aggregator.py`)** — `Aggregator.__init__` accepts an optional `ranging_source: Callable[[Contest, int], ChirpResult | None]`. The default `synthetic_ranging_source` produces deterministic results from a geometry map for demoing. Swapping in real mic capture (record the chirp, measure ToF, feed back `ChirpResult`) requires zero changes to `ranging.py` or `election.py`. The seam is documented at `aggregator.py:90`.
- **Contest detection in the tick loop** — after each `elect()` call, the aggregator checks whether the result is contested and, if so, fires a chirp (one per contest episode, respecting `CHIRP_FRESH_TICKS = 2` duty-cycle). The fusion result can override BLE's ownership decision, recording a `Handoff` so it surfaces in the existing handoff log and conversation-handoff FSM exactly as a tier-1 handoff would.
- **`--ranging-geometry` CLI flag** — declares per-scanner tier-2 geometry: `--ranging-geometry "A=1.5:in,B=2.5:out"` where `in` means the scanner hears the chirp and `out` means it's behind a wall (dropped from measurements). Wall demo: BLE ranks B as owner, geometry marks B as `out`, fusion returns `chirp-room-containment` and ownership flips to the in-room scanner.
- **New wire messages** — `{"type":"ranging",...}` broadcast (contest info, chirp results, fusion reason) plus one-shot `rangingEvent` (mirrors the `wakeOutcome`/`conversationEvent` pattern). Additive only — existing `ElectionMessage` and `ConversationMessage` untouched.
- **Dashboard ranging UI (`src/app/mesh/`)** — tier-2 ranging panel with contest readout, a `ChirpPing` animation between contested scanners, a room-containment badge when fusion overrides BLE, and a fusion-reason label in the scanners header.
- **Tests** — 85 pytest tests green: 12 election, 19 conversation, 24 pure ranging (incl. a wall-partition kill-test proving BLE cannot resolve what chirp can), 4 Phase-4 aggregator integration (contest fires + chirp overrides BLE owner, rangingEvent one-shot, ranging suppressed when uncontested, envelope shape), plus 5 parser/geometry tests and the 21 prior aggregator tests.

**Status:** Code complete, 85/85 tests green, dashboard compiles (`tsc --noEmit` + `next build` clean). Wall demo: `aggregator.py --ranging-geometry "A=1.5:in,B=2.5:out"`.

**Non-goals deferred to a later build:** real ultrasonic emission/capture (the `ranging_source` seam is ready; the mic/audio layer is the integration point), multi-chirp averaging (currently one chirp per contest episode), Kalman-style sensor fusion with UWB.

## Phase 5 — Open protocol spec

Aether's wire format and election algorithm are now documented independently of this repo's Python/TypeScript implementation, so a compatible scanner, aggregator, or client can be built in any language without reading the source.

- **[`PROTOCOL.md`](PROTOCOL.md)** — actors and topology (scanner / aggregator / client star topology, scanners never talk to each other), the full WebSocket message contract (`reading`, `lost`, `election`, `conversation`, `ranging`, inbound `wake`/`say`), the normative `elect()` decision procedure (hysteresis constants, candidacy rule, calibration offset, tie-break), the four-phase conversation handoff FSM, the tier-2 ranging fusion precedence ladder, a versioning/compatibility rule (additive-only: unrecognized types and fields must be ignored, not fatal), a reference-implementation map back to the actual source files, and an explicit non-goals section (no wake-word/STT/dialogue/TTS, no auth, no service discovery — Aether decides *who* answers, not *how*).
- Every schema and constant in the spec was cross-checked against the running code (`election.py`, `conversation.py`, `ranging.py`, `messages.py`, `aggregator.py`) rather than written from memory — including a subtle point that's easy to get wrong from the docstrings alone: `lastHandoff` is *not* one-shot like `wakeOutcome`/`conversationEvent`/`rangingEvent` — it persists across broadcasts until superseded by a newer handoff, verified directly against the aggregator's broadcast loop.

**Status:** Documentation only, no code changes. This closes the roadmap — all five phases are now ✅.
