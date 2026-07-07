# Architecture — Phase 9: Fusion Engine + Real Chirp

## New modules (all additive — ranging.py/election.py/conversation.py untouched)

```
aether-bridge/
├── chirp_audio.py           # NEW - generate 18-21kHz chirp waveform, matched-filter ToF detection
├── real_ranging_source.py   # NEW - wires chirp_audio.py into the RangingSource(Contest, tick) -> ChirpResult seam
├── fusion_2d.py              # NEW - 2-D Kalman filter, per-user (x,y) position + covariance, advisory only
├── room_adjacency.py         # NEW - learned scanner-pair co-occurrence, consulted before firing a chirp
└── aggregator.py             # UNCHANGED - already accepts ranging_source as an injectable constructor arg
```

## Data flow — real chirp (fills the existing seam, no new call sites needed)

```
aggregator.py's existing _ranging_loop (line 472, UNCHANGED)
  → self._ranging_source(contest, tick)     # aggregator.py:492, UNCHANGED call site
      → real_ranging_source.py's callable (passed in at construction instead of synthetic_ranging_source)
          → chirp_audio.emit_chirp()                          [sounddevice playback]
          → chirp_audio.detect_chirp(captured_samples)         [scipy.signal matched filter -> tof_us]
          → returns ChirpResult                                [ranging.py's existing dataclass, UNCHANGED]
  → ranging.py's chirp_from_measurements/fuse()   UNCHANGED - identical whether ChirpResult came from
    a real microphone or the synthetic source, per ranging.py's own docstring (line 25-26)
```

## Data flow — room adjacency (an optimization in front of the chirp, not a replacement)

```
Before _ranging_loop fires a new chirp for a Contest(incumbent, challenger):
  → room_adjacency.lookup(incumbent, challenger)
      → if confidently known (enough prior observations, consistent result) -> skip the real chirp,
        synthesize a ChirpResult directly from the learned adjacency (same_room bit already proven)
      → else -> fire the real chirp as normal, then room_adjacency.record(incumbent, challenger, result)
        to strengthen (or correct) the learned map for next time
```

## Data flow — 2-D fusion (advisory, alongside the existing 1-D election - not wired into ownership yet)

```
Each tick, for the currently-owning user's beacon identity:
  → fusion_2d.update(user_track, per_scanner_calibrated_distances, chirp_tof_if_any)
  → returns (x, y, covariance) - an ADVISORY position estimate
  → NOT consulted by election.py/aggregator._owner this phase (PRD's explicit out-of-scope:
    2-D tracking does not replace the proven 1-D hysteresis election this phase) - this is the
    data layer Phase 10's dashboard will visualize, built now so that phase isn't starting from zero.
```

## What is explicitly NOT built here
Any change to `ranging.py`, `election.py`, `conversation.py`, `aggregator.py`'s existing logic (only its already-existing `ranging_source` constructor parameter gets a new value passed in, from a CLI flag or `__main__` wiring — not a code change to the class itself unless the constructor wiring needs a one-line addition to pass the new source instead of the synthetic default). Dashboard visualization of the 2-D track (Phase 10). Wall-geometry auto-mapping beyond pairwise adjacency.

## Key risk called out inline
`chirp_audio.py`'s real emit/detect round-trip cannot be verified by a build agent without two physical speaker+mic devices in the same room — the matched-filter DSP algorithm itself is fully testable against synthesized signals with a known injected delay (prove the math is correct), but the actual acoustic round-trip through real air, real hardware, and real ambient noise is manual-verification only, exactly like Phase 8's wake-word mic accuracy was.
