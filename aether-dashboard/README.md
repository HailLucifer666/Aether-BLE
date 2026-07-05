# Aether Protocol — Cross-Device AI Arbitration Dashboard

**The problem:** Say "Hey Google" / "Alexa" / "Siri" in a room with three devices and they all wake up, answer over each other, or the wrong one wins. Big Tech arbitrates once, at the wake-word instant, in the cloud, inside a single vendor's walled garden — and it's been broken for a decade.

**The idea:** Arbitration should be a **continuous, local, vendor-agnostic proximity protocol.** You carry a BLE beacon (your phone). Every device in the room continuously ranks its signal strength to you. Exactly one device "owns" the AI conversation at any moment — and as you walk across the room, the conversation *hands off* to the nearest device, like a phone call roaming between cell towers.

This dashboard visualizes the full Aether Protocol stack: from single-device proximity arbitration (Phase 0/1) through multi-device mesh election (Phase 2), portable conversation handoff (Phase 3), and tiered BLE + ultrasound sensing (Phase 4).

## The money shot

The dashboard has an **arbitration toggle**:

- **Naive mode** — strongest signal wins instantly. Watch the active device flap back and forth whenever two devices are at similar distance, purely from RF noise. This is (roughly) the industry's failure mode.
- **Hysteresis mode** — a challenger must beat the active device by **5 dBm for 2 consecutive readings** before ownership transfers. Flapping gone. Deterministic single owner.

Same room, same walk, one toggle. That's the pitch.

## Run it

```bash
npm install
npm run dev
# open http://localhost:3000
```

Click **Run Simulation** to auto-walk the room (Phone → Speaker → back → PC, ~15 s), or click the track under the room map to move the user manually.

## How the simulation works

- RSSI = `baseRssi − distance × 6 + noise(±4 dBm)` — standard log-ish path loss with jitter
- Signals re-sampled every 600 ms; arbitration runs every tick
- Handoffs are logged with timestamps (last 20 kept)

## Data-source modes

The dashboard header cycles through three live data sources:

- **Room Preview** — client-side RSSI physics with a scripted room-walk. No hardware needed. Shows the Naive vs Hysteresis arbitration toggle.
- **Live BLE** — single real beacon over `ws://127.0.0.1:8765` (see `aether-bridge/bridge.py`). Real radio, real signal strength.
- **Mesh** — multi-scanner election viewer over `ws://127.0.0.1:8766` (see `aether-bridge/aggregator.py`). This is the default mode on load.

## Mesh mode features (Phase 2–4)

The mesh viewer at `src/app/mesh/` is strictly read-only — every field comes straight from the aggregator's broadcast. Key panels:

**Election (Phase 2)**
- Owner spotlight with pulsing border
- Ranked per-scanner signal cards with live RSSI and animated speaking-wave indicator
- Server-driven handoff log
- Wake button: sends `{type:"wake"}`, shows ACCEPTED for the owner and SUPPRESSED for all others

**Conversation (Phase 3)**
- "Say something" input sends `{type:"say","text":"..."}` to the aggregator
- Real `edge-tts` neural audio plays in the browser; on TRANSFER the audio pauses, on RELEASE it resumes from the same word — the audible "the sentence moved" moment
- 4-step phase pill: PREPARE → TRANSFER → CONFIRM → RELEASE
- Graceful offline path with "TTS offline" badge when `edge-tts` is unavailable

**Ranging (Phase 4)**
- Tier-2 ranging panel lights up when the BLE election is contested (two scanners within the hysteresis margin)
- ChirpPing animation between contested scanner cards
- Room-containment badge when chirp-based fusion overrides BLE's answer (the killer feature — proves a scanner is behind a wall)
- Fusion-reason label: `ble-only`, `chirp-confirmed`, `chirp-resolved-tie`, or `chirp-room-containment`

## Roadmap

| Phase | What | Status |
|---|---|---|
| 0 | Room Preview: Naive vs Hysteresis arbitration | ✅ Done |
| 1 | Live BLE: real beacon scanning via `bleak` | ✅ Built, hardware-verified |
| 2 | Multi-device mesh, leader election, wake suppression | ✅ Aggregator + election + mesh UI |
| 3 | Conversation state migrates on handoff (edge-tts, 4-phase FSM) | ✅ Audio + handoff + phase pill |
| 4 | Tiered sensing: BLE + near-ultrasound chirp fusion | ✅ Contest detection, ranging panel, room-containment |
| 5 | Open protocol spec | Planned |

## Stack

Next.js 15 (App Router) · React 19 · TypeScript · Tailwind CSS · Framer Motion · Lucide — no backend, no API, no database.
