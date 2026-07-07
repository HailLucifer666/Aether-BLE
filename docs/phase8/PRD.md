# PRD — Aether Phase 8: Real Assistant + Wake Word + HA Integration

## Problem
Phases 0-7 proved arbitration (which device owns a conversation) but never wired in a real assistant. Wake-word suppression is simulated via a keypress or a manual `{"type":"wake"}` WS message (`aggregator.py:trigger_wake`); TTS is a cloud call (edge-tts); there is no real LLM in the loop; nothing exists that Home Assistant can attach to. This phase makes the "problem actually fixed" claim demonstrable: say a wake word near two listening devices, exactly one responds, using a local LLM and local TTS, and the same mechanism works as a Home Assistant Assist satellite.

## Users
Solo builder. Test rig: desktop + laptop, both mic-equipped, both already able to run `aggregator.py`/`bridge.py`. Ollama is already installed locally with `gemma3:1b-it-qat` (fast) and `qwen3` (larger) models available.

## User Stories
1. As the user, I say a wake word near both machines; exactly one (the current owner) proceeds to listen, the other visibly suppresses — no manual trigger needed.
2. As the user, after the wake word, my spoken request gets a real LLM-generated reply (via local Ollama), not canned text.
3. As the user, the reply is spoken with local TTS (Piper), not a cloud call — the "no cloud" claim holds.
4. As a Home Assistant user, I can add an Aether node as a Wyoming satellite in HA's Assist pipeline, and Aether's ownership arbitration decides which satellite is "hot" at any moment.

## Acceptance Criteria
- `wake_listener.py`: a new per-node process that runs openWakeWord against live mic audio and sends `{"type": "wake"}` to the aggregator's existing WS endpoint on detection — reuses the wire message `aggregator.py:769` already handles, no wire protocol change. Debounced (a hard-coded minimum interval, e.g. 1.5s) so one spoken wake word heard by multiple near-simultaneous mics doesn't spam multiple wake events — proven with a unit test on the debounce logic in isolation (no mic/model needed for that specific test).
- openWakeWord model inference proven on at least one real audio fixture (a pretrained model's own bundled/downloadable test clip, or a synthesized negative-control tone confirmed NOT to trigger) — be explicit about which real-audio test actually ran, since a live microphone can't be driven by a build agent.
- `aggregator.py`'s `_generate_speech` is changed to try Piper (local, subprocess or python binding) first; edge-tts is removed per the "no cloud" design principle (this was always the intended replacement, not a new deferral); synthetic fallback (no audio, progress-clock-only) remains as the final fallback exactly as today. Proven by generating real audio bytes from a sample string and asserting non-empty valid audio output — this is fully testable without hardware.
- New `llm.py` (or similarly named module): given transcript context + new user text, calls the already-running local Ollama HTTP API (`http://localhost:11434`) with `gemma3:1b-it-qat` as the default model, returns generated reply text. Proven with a real call against the actually-running Ollama instance on this machine (not mocked) — if Ollama isn't reachable at test time, the test must skip cleanly with a clear message, not fail the whole suite.
- A new WS message type `ask` (`{"type": "ask", "text": "..."}`) triggers: LLM reply generation -> existing `_handle_say`-style TTS + conversation-FSM pipeline, assigned to the current owner exactly like the existing manual `say` path. The existing `say` path (direct text, no LLM) stays as-is for debugging/demos — this is additive, not a replacement.
- `wyoming_satellite.py`: a minimal Wyoming-protocol TCP server exposing Aether as a wake+TTS satellite (`detect`/`detection` and `synthesize` event types at minimum) that a real Home Assistant instance can add as an integration. Proven by a scripted client that speaks the Wyoming JSONL-over-TCP protocol against the running server and checks for correct event responses — full HA-side verification is out of scope for an agent (needs a real HA instance) and must be explicitly flagged as manual-verification-required.

## Out of Scope (this phase)
Real speech-to-text for the `ask` path's user input (the PRD assumes text input for now — STT wiring is a fast-follow once HA's own Assist pipeline, which already includes STT, is confirmed working through the Wyoming satellite path); full Wyoming audio-streaming fidelity beyond the minimal event set needed for HA to register the satellite; per-user wake-word personalization; output-arbitration (earbuds routing) — that's Phase 9+ per the original roadmap; changing `bridge.py`'s BLE beacon/scanner logic (untouched, Phase 6/7 scope).
