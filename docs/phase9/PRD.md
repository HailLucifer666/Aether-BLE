# PRD — Aether Phase 9: Fusion Engine + Real Chirp

## Problem
`ranging.py`'s tier-2 chirp math (`chirp_from_measurements`, `fuse`, `detect_contest`) is real, tested, deterministic logic — but it has never been fed by a real acoustic chirp. The aggregator's `_ranging_source` seam (`aggregator.py:293`, called at line 492) defaults to `synthetic_ranging_source`, a deterministic fake. Position tracking is also 1-D/RSSI-rank only — there is no 2-D position estimate, no per-scanner calibration learning, and no room-adjacency memory (every contest pays the full chirp cost, even for a wall the system has already proven exists many times before).

## Users
Solo builder. Test rig: desktop + laptop, both already speaker+mic equipped (needed for Phase 8's wake-word mic capture already). This phase turns that same hardware into a real chirp emitter/detector pair.

## User Stories
1. As the user, a staged behind-wall tie (two devices with equal RSSI, one genuinely in a different room) resolves correctly via a real emitted/detected near-ultrasound chirp, not a synthetic stand-in.
2. As the user, the system tracks a 2-D position estimate per user (not just a 1-D ranked list), fusing RSSI-derived distance and chirp ToF via a Kalman filter, with an honest uncertainty radius.
3. As the user, room-adjacency (which scanner pairs can/can't hear each other's chirps) is learned over repeated contests, so most future room-containment questions get answered without firing a new chirp at all.

## Acceptance Criteria
- New `chirp_audio.py`: generates a real 18-21kHz linear-chirp waveform and implements matched-filter time-of-flight detection against a recorded/captured signal. Proven with real numpy/scipy signal-processing tests using synthesized test signals with a known, injected delay — assert the detected ToF matches the injected delay within a stated tolerance. This is the part `docs/phase9`'s own design principle (mirroring the original roadmap note: "numpy/scipy only; feed with recorded traces in CI") makes fully agent-verifiable without live hardware.
- New `real_ranging_source.py` (or similar): wires `chirp_audio.py`'s emit/detect into the exact `RangingSource` callable signature `_ranging_source` already expects (`(Contest, tick) -> ChirpResult`), using `sounddevice` (already a Phase 8 dependency) for actual speaker playback + mic capture. This is the one piece that is explicitly NOT agent-verifiable without two real speaker+mic devices in the same room — state plainly what was and wasn't run for real.
- New `fusion_2d.py`: a 2-D Kalman filter (numpy, no new heavy dependency) fusing per-scanner RSSI-derived distance estimates (reusing the existing log-distance/calibration approach already implied by `ScannerState.calibrated_rssi()`) into a single (x, y) position + covariance per tracked user. Proven with a synthetic-trajectory unit test: feed a known ground-truth path through simulated noisy per-scanner distance readings, assert the filtered position error stays under a stated bound (target: <1.5m in a 2-scanner synthetic room, matching the original roadmap's own exit criterion) — this is fully testable without hardware.
- New `room_adjacency.py`: a simple learned co-occurrence map (which scanner-pairs' chirps get heard by each other across repeated contests) that the fusion path consults BEFORE firing a new chirp, when confidence is already high — reduces chirp frequency without changing `ranging.py`'s existing fusion contract. Proven with a unit test simulating N repeated contests between the same pair and asserting the learned adjacency converges and is then correctly consulted.
- Zero changes to `ranging.py`, `election.py`, `conversation.py` — this phase is purely additive (new modules + a new injectable ranging source + an optional 2-D tracking layer alongside the existing 1-D election, not a replacement of it).
- Full existing pytest suite still green (aside from the already-disclosed pre-existing `test_contest_fires_and_chirp_overrides_ble_owner` timing flake).

## Out of Scope (this phase)
Replacing the 1-D election/hysteresis logic with 2-D tracking as the primary arbitration path (2-D tracking is additive/advisory this phase, feeding future UX/dashboard work, not a replacement for `election.py`'s proven tier-1 logic). Multi-user position tracking beyond what the existing per-user beacon identity (Phase 6) already provides. Any dashboard/UI visualization of the 2-D position (Phase 10 per the original roadmap). Wall-geometry auto-detection beyond simple pairwise adjacency learning.
