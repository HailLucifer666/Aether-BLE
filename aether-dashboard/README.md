# Aether Protocol v0.1 — Cross-Device AI Arbitration

**The problem:** Say "Hey Google" / "Alexa" / "Siri" in a room with three devices and they all wake up, answer over each other, or the wrong one wins. Big Tech arbitrates once, at the wake-word instant, in the cloud, inside a single vendor's walled garden — and it's been broken for a decade.

**The idea:** Arbitration should be a **continuous, local, vendor-agnostic proximity protocol.** You carry a BLE beacon (your phone). Every device in the room continuously ranks its signal strength to you. Exactly one device "owns" the AI conversation at any moment — and as you walk across the room, the conversation *hands off* to the nearest device, like a phone call roaming between cell towers.

This repo is the **Phase 0 demo**: a pure client-side dashboard that simulates BLE RSSI physics and visualizes the arbitration in real time. No hardware required.

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

## Roadmap

| Phase | What | Status |
|---|---|---|
| 0 | This visual simulator | ✅ complete |
| 1 | Real BLE: phone advertises (nRF Connect), Windows scans via `bleak`, live dashboard over WebSocket | built, pending hardware verification |
| 2 | Multi-device mesh, leader election, wake-word suppression on losing devices | ✅ in progress — `src/app/mesh/` viewer live |
| 3 | Conversation state migrates on handoff — the assistant finishes its sentence on the next device | planned |
| 4 | Tiered sensing: BLE always-on + near-ultrasound (18–21 kHz) chirp tie-breaker for contested elections | planned |
| 5 | Open protocol spec | planned |

## Data-source modes

The dashboard header cycles through three live data sources:

- **Simulation** (Phase 0) — client-side fake RSSI physics, no hardware.
- **Live BLE** (Phase 1) — single real beacon over `ws://127.0.0.1:8765` (see `aether-bridge/bridge.py`).
- **Mesh** (Phase 2) — read-only multi-scanner election viewer over `ws://127.0.0.1:8766` (see `src/app/mesh/` and `aether-bridge/aggregator.py`). Renders the current owner, a ranked per-scanner signal list, a server-driven handoff log, and a Wake button (the dashboard's only outbound message). Never arbitrates ownership locally — every field comes straight from the aggregator's `election` broadcast.

## Stack

Next.js 15 · React 19 · TypeScript · Tailwind CSS · Framer Motion · Lucide — no backend, no API, no database.
