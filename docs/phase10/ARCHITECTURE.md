# Architecture — Phase 10: UX v2

## New backend modules
```
aether-bridge/
├── layout.py               # NEW - persisted scanner (x,y) + calibration, ~/.aether/layout.json
└── aggregator.py           # MODIFIED - FusionTracker instance, position broadcast, 4 new
                             #            client->server message handlers, tuning params
                             #            promoted from module constants to instance fields
```

## Data flow — position broadcast (fills fusion_2d's dormant call site)
```
Each election tick (aggregator._election_tick_loop, existing):
  → for each scanner with a live calibrated_rssi(): convert to distance via
    layout.py's stored rssiAt1m/pathLossExponent for that scanner
  → self._fusion_tracker.update(user_id, tick, scanner_positions, scanner_distances,
      chirp_scanner_id=<set when self._last_chirp is fresh>)
  → _broadcast_loop: if a track exists, attach a `position` message (same
    gating pattern as `ranging`/`conversation` - omitted when nothing to send)
```

## Data flow — device placement (new client->server input, LAN-trust model unchanged)
```
Dashboard /setup wizard: drag icon -> placeDevice{scannerId, x, y}
  → aggregator validates finite + bounded (e.g. |x|,|y| <= 1000m) -> layout.py.set_position()
  → layout.py rewrites ~/.aether/layout.json (same pattern as room_adjacency.py)
  → next election tick's fusion update uses the new position; no restart needed
Same shape for setCalibration{scannerId, rssiAt1m, pathLossExponent}.
```

## Data flow — live tuning (new client->server input)
```
Signal Lab sliders -> setTuning{hysteresisDb, consecutiveTicks, contestMarginDb}
  → aggregator validates ranges (e.g. hysteresisDb in [0,20]) -> updates self._tuning fields
  → election.py's tick logic (unchanged file) reads these via a passed-in tuning object
    instead of the module constants - the ONLY change to election.py this phase: the
    constants become the *default* values of a small dataclass instead of bare module
    globals, so a per-instance override is possible without touching the arbitration
    logic itself.
```

## Frontend structure
```
aether-dashboard/src/app/
├── page.tsx            # MODIFIED - redirects to /spatial (was the single-file demo)
├── mesh/                # UNCHANGED - existing fallback viewer, byte-for-byte preserved
├── spatial/             # NEW - floor plan + live position dot + ownership halo
├── signal-lab/          # NEW - RSSI history charts + tuning sliders
├── timeline/            # NEW - scrubbable client-side event log + JSON export
├── setup/               # NEW - device placement + calibration wizard
└── lib/
    └── useAetherSocket.ts   # NEW (or useElectionSocket.ts extended) - adds position/
                              # placeDevice/setCalibration/setTuning to the existing
                              # single-socket contract; every route consumes this one hook
```

## What is explicitly NOT built here
Any change to `election.py`'s owner-decision logic (only its constants move to an overridable dataclass — the comparison logic itself is untouched). A live guided walk-the-room calibration flow. Backend-side trace record/replay. BLE Channel Sounding. Output-arbitration (earbuds) UI — no backend exists for it yet.

## Key risk called out inline
`position` data is only as good as `fusion_2d.py`'s EKF, which itself has never been fed real hardware RSSI/chirp traces end-to-end (Phase 9 disclosed the same limitation for `real_ranging_source.py`). The Spatial View's uncertainty halo will be honest about the covariance the filter actually computes, but "does the dot track a real walking human accurately" remains manual-verification-only, same as every prior phase's hardware-dependent claim.
