# Architecture — Phase 8: Real Assistant + Wake Word + HA Integration

## New processes (each mirrors bridge.py's existing per-node pattern)

```
aether-bridge/
├── wake_listener.py         # NEW - per-node process, mic -> openWakeWord -> WS "wake" to aggregator
├── llm.py                   # NEW - transcript + user text -> Ollama HTTP call -> reply text
├── wyoming_satellite.py     # NEW - Wyoming-protocol TCP server, bridges to aggregator's wake/say
├── aggregator.py            # MODIFIED - _generate_speech swaps edge-tts -> Piper; new "ask" WS handler
└── requirements.txt         # MODIFIED - add openwakeword, sounddevice, piper-tts, wyoming; remove edge-tts
```

## Data flow — wake detection (reuses the existing wire message, zero protocol change)

```
wake_listener.py (per node, own process)
  → sounddevice captures live mic frames
  → openWakeWord model.predict(frame) each chunk
  → on score > threshold AND debounce window elapsed:
      ws.send(json.dumps({"type": "wake"}))          # exact message aggregator.py:769 already handles
  → aggregator.trigger_wake() runs UNCHANGED - existing owner-vs-suppressed logic (aggregator.py:588-602)
    is correct as-is: a real spoken wake word is genuinely heard by every nearby mic near-simultaneously,
    so "all present scanners see one wake event, only the owner is ACCEPTED" already matches reality.
```

## Data flow — real assistant turn (new, additive alongside the existing manual "say")

```
Client sends {"type": "ask", "text": "..."}
  → aggregator._handle_ask(text)
      → llm.generate_reply(transcript_context, text)      [llm.py -> Ollama HTTP, gemma3:1b-it-qat]
      → reply_text
      → _handle_say(reply_text)                            [UNCHANGED existing method]
          → _generate_speech(reply_text)                   [MODIFIED: Piper first, synthetic fallback]
          → start_utterance(...)                            [UNCHANGED conversation.py FSM]
```

## Data flow — Home Assistant Wyoming satellite

```
Home Assistant Assist pipeline
  → Wyoming TCP connection to wyoming_satellite.py
  → "detect" event registers the satellite's wake capability
  → on Aether-side wake acceptance (owner match), emit "detection" event to HA
  → HA's own STT/intent/LLM pipeline runs (Aether does NOT duplicate this - Aether's job here
    is purely "am I the arbitrated owner right now", not replacing HA's Assist brains)
  → HA sends "synthesize" event back with response text
  → wyoming_satellite.py calls the SAME Piper TTS path aggregator.py now uses, plays/streams audio back
```

## What is explicitly NOT built here
Real STT for the standalone (non-HA) `ask` path — text input only, this phase. Full Wyoming audio-streaming fidelity beyond the minimal event set HA needs to register and use the satellite. Output arbitration (earbuds routing) - Phase 9+. Any change to `bridge.py`, `beacon_auth.py`, `realm.py`, `pairing.py`, `discovery.py`, `identity.py`, or `election.py` - all Phase 6/pre-existing scope, untouched.

## Key risk called out inline
`aggregator.py`'s `_generate_speech` already has a deliberate try/fallback structure (`aggregator.py:645-654`) specifically so the aggregator runs even if the TTS dependency is missing/broken. The Piper swap MUST preserve that exact resilience property — Piper failing (model file missing, subprocess error) should fall back to the existing synthetic-utterance path, never crash the aggregator or block the conversation FSM.
