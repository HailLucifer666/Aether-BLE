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
