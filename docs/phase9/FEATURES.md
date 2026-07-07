# Features — Phase 9 (real chirp DSP is the MVP; 2-D fusion and adjacency are additive)

1. **[MVP] Real chirp waveform generation + matched-filter ToF detection** — `chirp_audio.py`, fully verifiable against synthesized test signals with known injected delay.
2. **[MVP] Real ranging source wiring** — `real_ranging_source.py`, fills the existing `_ranging_source` seam; live acoustic round-trip is manual-verification only (needs two real speaker+mic devices).
3. **[MVP] 2-D Kalman fusion** — `fusion_2d.py`, advisory position + covariance per user, fully testable via synthetic trajectories, not wired into ownership arbitration this phase.
4. **[MVP] Room-adjacency learning** — `room_adjacency.py`, reduces future chirp frequency once a scanner-pair's containment relationship is confidently learned.
5. **[Deferred] Dashboard visualization of the 2-D track** — Phase 10 per the original roadmap.
6. **[Deferred] 2-D tracking replacing 1-D election as the arbitration path** — advisory only this phase; `election.py`'s proven hysteresis logic remains authoritative.
7. **[Deferred] Wall-geometry auto-mapping beyond pairwise adjacency** — not this phase.
