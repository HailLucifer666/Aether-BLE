# Features — Phase 8 (MVP list; HA/Wyoming is the strategic piece, not a stretch goal)

1. **[MVP] Real wake-word detection** — `wake_listener.py`, openWakeWord on live mic audio, debounced, sends the existing `wake` WS message.
2. **[MVP] Local TTS** — Piper replaces edge-tts in `aggregator.py`, same fallback resilience preserved.
3. **[MVP] Local LLM replies** — `llm.py`, Ollama-backed, new `ask` WS message additive to existing `say`.
4. **[MVP] Home Assistant Wyoming satellite** — `wyoming_satellite.py`, minimal wake+TTS event set, real HA registration is manual-verification (no HA instance available to an agent).
5. **[Deferred] Real STT for the `ask` path** — text input only this phase; HA's own Assist pipeline already has STT for the Wyoming path.
6. **[Deferred] Output arbitration (earbuds)** — Phase 9+ per roadmap.
7. **[Deferred] Per-user wake-word personalization** — multi-user wake voice profiles, not this phase.
