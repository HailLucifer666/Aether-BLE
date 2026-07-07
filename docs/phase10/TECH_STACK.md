# Tech Stack — Phase 10

## Backend (`aether-bridge/`)
- **Layout persistence:** plain dict → JSON at `~/.aether/layout.json`, same pattern as `room_adjacency.py`/`realm.py`/`beacon_auth.py`. No new dependency.
- **Fusion wiring:** consumes the already-built `fusion_2d.FusionTracker` as-is (no changes to `fusion_2d.py` itself — it was built in Phase 9 specifically for this call site). Distance-from-RSSI reuses the existing log-distance model already implied by `election.ScannerState.calibrated_rssi()`; `layout.py` supplies the per-scanner `rssiAt1m`/path-loss-exponent inputs that model needs.
- **Live tuning:** existing `HYSTERESIS_*`/`CONTEST_MARGIN_DB`-style module constants become instance-level mutable fields on `Aggregator`, defaulted from the current constants, overridable via `setTuning`. No new dependency; this is a refactor of where the numbers live, not new math.
- **Testing:** pytest, `test_layout.py`, plus new cases in `test_aggregator.py` for `position`/`placeDevice`/`setCalibration`/`setTuning` handling.

## Frontend (`aether-dashboard/`)
- Next.js 15 App Router, React 19, Tailwind 3, Framer Motion — all already in `package.json`, no version bump.
- Routing: real routes under `src/app/{spatial,signal-lab,timeline,setup}/`, each a client component consuming a shared WS hook (extend `useElectionSocket` with the new message types rather than forking it — one socket connection, one source of truth, matching the existing hook's own doc comment about never arbitrating locally).
- Charts (Signal Lab's RSSI history): hand-rolled SVG/Canvas rendering of the rolling buffer, no new charting library — this codebase's existing preference (see `lib/rssi.ts`'s smoothing already being hand-rolled) and keeps bundle size down for a PWA.
- Floor plan (Spatial View): plain SVG with drag via pointer events (no dnd library needed for "drag an icon on a 2-D plane").
- PWA: a hand-written `manifest.json` + minimal service worker (cache-the-app-shell only) rather than `next-pwa` — one new file pair, no new dependency, matches this project's "pick the platform-native option" precedent (Phase 7's TECH_STACK.md, Phase 9's hand-rolled Kalman filter).
- Skills to invoke during the build: `ui-ux-pro-max` / `frontend-design` per the original blueprint's UX section note.

## Rationale
Every new backend module and every new frontend dependency choice stays inside the existing footprint (numpy/scipy/websockets already present; Next.js/Tailwind/Framer Motion already present) — no new package.json/requirements.txt entries this phase, consistent with every prior phase's stated preference for hand-rolling well-understood pieces over pulling in a framework.
