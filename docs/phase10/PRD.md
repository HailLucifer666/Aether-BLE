# PRD — Aether Phase 10: UX v2

## Problem
Phases 6–9 built real security, a real Android node, a real assistant/wake pipeline, and a real 2-D fusion engine (`fusion_2d.py`) — but `fusion_2d.py` has zero call sites (confirmed via grep: `FusionTracker` is never instantiated in `aggregator.py`) and there is no concept of scanner *position* anywhere in the codebase (election.py is RSSI-rank-only, no x/y). The dashboard (`aether-dashboard/`) is still the single-file demo viewer from Phase 0–4: it shows RSSI bars and a handoff log, not a spatial picture. Phase 9's own FEATURES.md explicitly deferred "dashboard visualization of the 2-D track" to this phase.

## Users
Solo builder, same test rig as prior phases (desktop + laptop, phones). This phase is the visible payoff of Phases 6–9 — the first time all of it becomes something a viewer can actually see and understand at a glance.

## User Stories
1. As the user, I place my scanners on a 2-D floor plan once (drag-to-place, persisted), so the aggregator has real geometry to fuse RSSI/chirp distances against.
2. As the user, I see a live position dot with an honest uncertainty halo (from `fusion_2d`'s covariance) that moves as I move, and a cyan ownership halo that visibly travels between devices on handoff.
3. As the user, I can open a "Signal Lab" view and see per-scanner RSSI over the last 60s, the smoothing/hysteresis band, and live tuning sliders that actually affect the running aggregator's election behavior.
4. As the user, I can scrub a timeline of recent elections/handoffs/contests/chirps and export the visible window as JSON.
5. As the user, the dashboard installs as a PWA on a phone and the existing mesh viewer keeps working unmodified as a fallback view.

## Acceptance Criteria — Backend (additive; existing wire messages untouched)
- New `layout.py`: persisted scanner (x, y) placement + per-scanner RSSI-at-1m/path-loss-exponent calibration, JSON at `~/.aether/layout.json`, same load-eager/rewrite-on-write pattern as `room_adjacency.py`/`realm.py`.
- `aggregator.py` gains a `FusionTracker` instance, fed each tick from existing per-scanner `calibrated_rssi()` values converted to distance via the placed layout, chirp ToF distance when a fresh chirp exists (higher-precision override, matching `fusion_2d.update_from_scanner_distances`'s `chirp_scanner_id` param). Zero changes to `election.py`'s owner arbitration — this is the same "advisory alongside 1-D election" contract Phase 9 already established.
- New `position` WS broadcast message (one per tracked user with an active track), gated the same way `ranging`/`conversation` already are (omitted entirely when no track exists yet, so the wire stays quiet for a fresh install with no placed layout).
- New client→server `placeDevice {scannerId, x, y}` and `setCalibration {scannerId, rssiAt1m, pathLossExponent}` messages (same trust model as the existing unauthenticated `say`/`wake` messages — LAN-trust, not a new gap). Validate numeric bounds (finite, within a sane meter range) before writing to `layout.py`, so a malformed message can't corrupt the persisted file or crash the tick loop.
- New live-tunable hysteresis parameters (`hysteresisDb`, `consecutiveTicks`, `contestMarginDb`) settable via a `setTuning` message, applied to the running election loop immediately (read from mutable instance state each tick rather than the current module-level constants).

## Acceptance Criteria — Frontend (`aether-dashboard/`)
- Break `page.tsx` into a real routed app: `/spatial` (default), `/signal-lab`, `/timeline`, `/setup` (placement + calibration wizard), plus the existing `/mesh` kept working byte-for-byte as a fallback view (already isolated in its own route folder).
- **Spatial View**: 2-D canvas/SVG floor plan; drag-to-place scanner icons (persists via `placeDevice`); live position dot(s) from `position` messages with an uncertainty-radius halo; ownership shown as a cyan halo that animates (Framer Motion spring) between devices on `lastHandoff`; chirp ripple animation reusing the existing `rangingEvent` one-shot.
- **Signal Lab**: rolling 60s RSSI history per scanner (raw vs. smoothed), hysteresis threshold band drawn on the chart, sliders for `hysteresisDb`/`consecutiveTicks`/`contestMarginDb` wired to `setTuning`.
- **Timeline**: client-side rolling log (already-received election/conversation/ranging/position messages, capped buffer) rendered as a scrubbable strip; "export JSON" downloads the visible buffer. No backend replay-ingestion this phase (flagged below).
- **Setup wizard**: mDNS-style device list (reuses whatever the aggregator already exposes as "known scanners" from the election message's `scanners[]` — no new discovery mechanism), drag-to-place onto the floor plan, numeric calibration inputs per scanner. A live guided "walk the room" auto-calibration flow is explicitly NOT built this phase (see Out of Scope) — manual placement + numeric calibration is the real, honestly-buildable version.
- PWA: manifest + service worker (offline shell only, live data still requires the WS connection) so it installs on a phone home screen.
- Design system: near-black `#0a0f1e` base, glass panels, cyan-400/amber-500/violet-400/red-400 semantic colors — per the original blueprint's UX section. Skill: `ui-ux-pro-max`/`frontend-design`.

## Out of Scope (this phase)
Live guided "walk-the-room" calibration (needs real-time hardware feedback loop this project has no way to verify without a physical walk — manual numeric calibration is the honest substitute). Backend trace record/replay for the Timeline (client-side buffer only). Multi-user position tracking beyond one track per beacon identity (already the Phase 9 ceiling). BLE Channel Sounding. Output-arbitration UI (earbuds routing) — no backend for it exists yet. Any change to `election.py`'s owner arbitration logic itself.

## Verification
- Backend: pytest for `layout.py` (persistence round-trip, bounds validation) and the new `position`/`placeDevice`/`setCalibration`/`setTuning` message handling in `aggregator.py` (unit tests with a fake track, no real hardware needed — same discipline as Phase 9's `fusion_2d.py` tests). Full existing suite still green (aside from the disclosed pre-existing flaky test).
- Frontend: `npm run build` clean, `tsc --noEmit` clean, existing `/mesh` route unmodified and still functional, Lighthouse PWA/perf check where the dev server allows it.
