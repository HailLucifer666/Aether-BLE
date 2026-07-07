# Features — Phase 10 (Spatial View is the MVP; Signal Lab/Timeline/Setup/PWA are additive)

1. **[MVP] `layout.py` — scanner placement + calibration persistence.** Fully testable (pure JSON round-trip, bounds validation), no hardware needed.
2. **[MVP] Fusion wiring into `aggregator.py`.** `FusionTracker` gets a real call site; `position` broadcast fills the dormant Phase 9 data layer. Testable with fake scanner readings; the EKF math itself was already proven in Phase 9.
3. **[MVP] Spatial View.** Floor plan, drag-to-place, live position dot + uncertainty halo, animated ownership halo, chirp ripple.
4. **[MVP] Setup wizard (manual placement + calibration).** Not the live "walk it" flow — that's explicitly deferred (see PRD Out of Scope).
5. **[Additive] Signal Lab** — RSSI history + live tuning sliders wired to a real `setTuning` message.
6. **[Additive] Timeline** — client-side scrubbable event log + JSON export. No backend replay this phase.
7. **[Additive] PWA manifest + service worker.**
8. **[Deferred] Live guided walk-the-room calibration** — needs a real-time hardware feedback loop; honestly unverifiable without a physical walk-through, same caveat class as Phase 9's live chirp round-trip.
9. **[Deferred] Backend trace record/replay for Timeline** — a real new subsystem, not a UI-layer task; candidate for a future "Phase 10.5" or folded into Phase 11's conformance-suite golden traces.
10. **[Deferred] Output-arbitration (earbuds) UI** — no backend exists for it; blueprint's §3.6, not this phase.
