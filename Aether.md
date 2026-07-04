\# AETHER PROTOCOL — Architecture Plan, Gap Analysis \& Demo Build



\## 1. Context \& Vision



\*\*The problem:\*\* Say "OK Google" or "Alexa" in a room with 3 devices and multiple devices wake, answer over each other, or the \*wrong\* one answers. Big Tech has band-aided this for a decade but never solved it, because their fix runs \*\*once, at the wake-word moment, inside a single vendor's walled garden\*\*.



\*\*The thesis (the innovation):\*\* Device arbitration shouldn't be a one-shot guess at wake time. It should be a \*\*continuous, local, vendor-agnostic proximity protocol\*\* where the conversation \*itself\* is a first-class object that follows the user through physical space — like a phone call handing off between cell towers. The user carries a BLE beacon (their phone); every device continuously ranks its proximity; exactly one device "owns" the conversation at any moment; ownership (and conversation context) hands off as the user moves.



\*\*What exists so far\*\* (per the user's complete handoff document, provided in conversation):

\- Validated Python CLI simulator (`F:\\BLE Hack\\aether\_simulator.py`) — RSSI path-loss model, noise, handoff detection; 4 clean handoffs across a 13-position room walk.

\- Real BLE beacon working: nRF Connect on OnePlus 7T, device name `AetherUser1`, TX 0 dBm (device cap), 250 ms advertising interval. Known quirk: advertising stops on screen lock.

\- Real BLE \*scanning\* blocked: Windows BLE stack + `bleak` failed to detect the advertisement → Phase 1 (real BLE) will target Linux/WSL2 or Raspberry Pi, not Windows.

\- Smart-ring hardware idea explicitly abandoned ($50K+/12mo); phone-as-beacon chosen. Strategy: protocol is the product; open-source it; hardware later, if ever.

\- \*\*Phase 0 exit criteria (user's goal):\*\* dashboard built → 60-second screen recording → post to GitHub/HN/X/Reddit to validate interest and find a co-founder. The demo is a pitch asset, so visual polish matters.



\*\*What we build now (Phase 0):\*\* a visual web dashboard demo per AETHER\_SPEC.md — pure client-side Next.js, simulated BLE, for pitching/validating the concept.



\---



\## 2. Gap Analysis — why Google, Amazon, and Apple haven't fixed this



| | Amazon (Alexa ESP) | Google (Home arbitration) | Apple (Siri/HomePod) | \*\*Aether\*\* |

|---|---|---|---|---|

| \*\*How it picks a device\*\* | "Echo Spatial Perception" — compares wake-word audio energy/SNR across Echos, cloud-side | Compares wake-word loudness/confidence across devices, cloud-assisted | Wake-word loudness + UWB proximity if an iPhone/Watch is present | Continuous BLE RSSI ranking, fully local |

| \*\*When arbitration happens\*\* | Only at the wake-word instant | Only at the wake-word instant | Only at the wake-word instant | \*\*Continuously\*\* — before, during, and after the utterance |

| \*\*Mid-conversation handoff\*\* | ❌ You're locked to the device that won | ❌ Same | ❌ Same | ✅ Conversation follows you room-to-room |

| \*\*Cross-vendor\*\* | Echo-only | Google-only | Apple-only | \*\*Open protocol\*\* — any device that can scan BLE can join |

| \*\*Cloud dependency\*\* | Yes — arbitration resolved server-side | Yes | Partially | \*\*None\*\* — local mesh consensus |

| \*\*Failure mode\*\* | Similar distances → wrong Echo wins; all Echos chime | Multiple devices light up / answer | HomePod answers when you meant your phone | Hysteresis prevents flapping; deterministic single owner |

| \*\*User position model\*\* | None (audio snapshot only) | None | UWB, but only iPhone↔HomePod | Persistent position estimate from RSSI (later: fused sensors) |



\*\*The gap in one sentence:\*\* every incumbent treats arbitration as \*"which microphone heard the wake word loudest, right now, among my own devices"\* — nobody treats it as \*"where is the user, continuously, across all devices, with the conversation as a portable object."\* That's the wedge.



\*\*Why incumbents can't easily follow:\*\* their business model requires the walled garden (arbitration = ecosystem lock-in), and their arbitration is coupled to cloud speech pipelines. An open, local, beacon-based protocol is structurally the thing they won't build.



\---



\## 3. The 100x Version — Full System Architecture (Roadmap)



\### Phase 0 — Visual Simulator (THIS BUILD)

Next.js dashboard, simulated RSSI, proves the arbitration logic and demos the story. Zero hardware.



\### Phase 1 — Real BLE, One Room

\- User's phone advertises BLE (nRF Connect `AetherUser1` beacon — already working, 0 dBm, 250 ms interval).

\- Scanner host: \*\*Linux/WSL2 or Raspberry Pi\*\*, not Windows — Windows BLE stack already proven unreliable for passive scanning in this project. Python `bleak` reads real RSSI, streams to the dashboard over a local WebSocket → the Phase 0 dashboard becomes a \*live\* room map with minimal changes.

\- Known constraints to engineer around: phone advertising stops on screen lock (needs foreground service/wakelock), raw indoor RSSI is noisy from multipath (EMA/Kalman smoothing + the same hysteresis arbitration proven in Phase 0).



\### Phase 2 — Multi-Device Mesh \& Leader Election

\- Each device runs a tiny Aether agent (Python/Rust daemon).

\- Agents gossip their smoothed RSSI readings over local network (mDNS discovery + UDP multicast).

\- Deterministic election: highest smoothed RSSI wins, hysteresis margin prevents flapping, tie-break by device ID. \*\*Losers suppress their wake-word response\*\* — this is the moment the original problem is actually fixed.



\### Phase 3 — Conversation as a Portable Object

\- Conversation state (transcript, LLM context, TTS mid-stream position) serialized and migrated on handoff — the assistant literally finishes its sentence on the next device.

\- Handoff contract: `PREPARE → TRANSFER → CONFIRM → RELEASE` (two-phase, so audio never plays on two devices at once).



\### Phase 4 — Tiered Sensing: BLE + Near-Ultrasound Tie-Breaker (the sonar idea, affordably)

User's insight: an inaudible tone emitted by devices' existing speakers, caught by existing mics. Refined into a power-efficient two-tier design:

\- \*\*Tier 1 (always on): BLE advertising\*\* — microwatt-cheap (BLE ≠ classic Bluetooth; beacons run years on coin cells). Continuous coarse proximity ranking; resolves \~80% of arbitrations alone.

\- \*\*Tier 2 (on demand): near-ultrasound chirp, 18–21 kHz\*\* — fired ONLY when an election is contested (two devices within the hysteresis margin). Challenger emits a 50–100 ms coded chirp; phone mic listens \~1 s; time-of-flight ranges to cm accuracy. Duty cycle <1%.

\- \*\*Key physics advantage:\*\* near-ultrasound doesn't pass through walls → hearing the chirp proves \*same-room presence\*, the one bit RSSI fundamentally cannot provide (2 m away in-room vs. 2 m behind a wall look identical to BLE). Room containment + ToF settles ties deterministically.

\- Plugged-in devices (speaker, PC) do the expensive emitting/listening; the battery-powered phone only advertises BLE and briefly listens when polled.

\- Prior art proving feasibility on commodity hardware: Cisco Webex ultrasonic room pairing, Chromecast guest-mode tokens, Alexa ultrasound presence detection.

\- Kalman filter fuses BLE RSSI + chirp ToF + UWB (where present) into one user-position estimate. This is the "expensive sonar" idea achieved with hardware people already own.

\- \*\*Demo hook for Phase 0/1 dashboards:\*\* visualize the escalation — when two signal bars are within the margin, show a "chirp" ping animation resolving the tie.



\### Phase 5 — Open Protocol Spec

\- Publish the arbitration + handoff wire protocol so any vendor/device can implement it. The moat is the protocol and the network effect, not the hardware.



\*\*Each phase is independently demoable and cheap\*\* — critical for a solo builder: Phase 0 costs $0, Phase 1 needs only the phone + PC already owned.



\---



\## 4. Phase 0 Build Plan (execute now)



\*\*User decision (asked \& answered):\*\* arbitration implemented \*\*both ways with a UI toggle\*\* — Naive mode (spec-exact instant strongest-wins, visibly flaps due to ±4 dBm noise) vs. Hysteresis mode (challenger must beat active by 5 dBm for 2 consecutive ticks). The toggle IS the pitch: show the industry's chaos, flip the switch, show the fix.



`F:\\Aether BLE` is empty — greenfield, not a git repo.



\### Deliverables



1\. \*\*`F:\\Aether BLE\\AETHER\_SPEC.md`\*\* — the spec saved verbatim as provided.

2\. \*\*`F:\\Aether BLE\\HANDOFF.md`\*\* — the user's complete handoff document saved verbatim (project memory: problem definition, research tables, BLE findings, decisions, lessons, roadmap).

3\. \*\*`F:\\Aether BLE\\aether-dashboard\\`\*\* — runnable Next.js project (structure per handoff section 5):



```

aether-dashboard/

├── README.md             # what Aether is, how to run the demo, roadmap TL;DR

├── package.json          # next@15, react@19, framer-motion, lucide-react, tailwindcss v3

├── next.config.js

├── postcss.config.js

├── tailwind.config.ts    # content: ./src/\*\*/\*.{ts,tsx}

├── tsconfig.json

└── src/app/

&#x20;   ├── layout.tsx        # metadata: "Aether Protocol v0.1", dark <html>

&#x20;   ├── globals.css       # @tailwind directives + custom scrollbar

&#x20;   └── page.tsx          # 'use client' — ALL logic \& UI (spec constraint: single file)

```



README.md is included because the stated goal is posting the repo publicly (GitHub/HN) — a repo without a README can't be pitched. PRD.md from the handoff structure is skipped for now; HANDOFF.md already carries that content.



Scaffold written by hand (not create-next-app — spec dictates exact structure), then `npm install`. Tailwind \*\*v3\*\* (spec's `tailwind.config.ts` implies v3 config style, not v4 CSS-first). The spec's single-file constraint (section 10) intentionally overrides the global many-small-files rule.



\### page.tsx design (spec sections 3–7, 10)



\*\*Data:\*\* `DEVICES` const — PC(pos -2, base -45), Phone(0, -55), Speaker(3, -50); lucide icons (Monitor, Smartphone, Speaker); per-device tailwind bar color. All state updates immutable.



\*\*State:\*\* `userPosition` (-2.5…3.5), `activeDevice: string | null`, `handoffs: HandoffEvent\[]` (cap 20), `isSimulating`, `hysteresisOn` (default ON), live per-device RSSI readings, challenger-streak counter (ref) for hysteresis.



\*\*Logic (spec section 5 formulas, verbatim):\*\*

\- `calculateRssi(device, userPos)` = `baseRssi - distance\*6 + (Math.random()-0.5)\*8`

\- `getBars(rssi)` = clamp 0–5 of `floor((rssi+90)/8)`

\- Ticker: `useEffect` interval (\~600ms) recomputes all RSSI at current position.

\- Arbitration per tick (constants confirmed in handoff doc: `HYSTERESIS\_THRESHOLD\_DB = 5`, `HYSTERESIS\_CONSECUTIVE = 2`):

&#x20; - \*Naive:\* best RSSI ≠ active → immediate handoff.

&#x20; - \*Hysteresis:\* challenger must exceed active by \*\*5 dBm\*\* for \*\*2 consecutive ticks\*\* (per the `shouldHandoff` pattern in the handoff doc); challenger/streak reset when the active device retakes the lead. First contact → strongest wins immediately.

\- Handoff → prepend `{from, to, rssi, time: HH:MM:SS}`, slice to 20.



\*\*Simulation (section 6):\*\* path `\[0, 0.5, 1, 2, 2.5, 3, 2, 1, 0, -1, -2, -2.5, -2]`, advance every 1.2s, button disabled while running.



\*\*UI (section 3):\*\* dark `bg-slate-950`, `max-w-6xl` centered —

\- \*\*Header:\*\* "Aether Protocol v0.1" / "Cross-Device AI Arbitration" + arbitration toggle (Naive ↔ Hysteresis) + "Run Simulation" button.

\- \*\*Room map (60%):\*\* horizontal axis -3…4m; device icons at positions; active device scale 1→1.2 + pulsing white border (Framer Motion); amber user dot with spring transition (\~0.5s); clickable track maps clickX → position.

\- \*\*Signal panel (40%):\*\* 3 rows — name, animated bar (0–5 bars → width %), live dBm, distance; active row cyan-highlighted.

\- \*\*Handoff log (full width):\*\* scrollable, max 20, entries slide in from left, format `HH:MM:SS  PC → Phone  (-58 dBm)`.



Fully typed, no `any`. Only `useState`/`useCallback`/`useEffect`/`useRef`; `motion.div` for all animation; lucide-react icons; cyan-400/500 accents, amber-500 user dot.



\### Verification



1\. `npm install` in `aether-dashboard`, start dev server via `preview\_start` (create `.claude/launch.json`: `npm run dev`, port 3000).

2\. `preview\_snapshot`/`preview\_screenshot`: header, room map, signal panel, log render on dark theme; `preview\_console\_logs` clean.

3\. Click "Run Simulation" → dot walks the path; handoffs logged ≈ Phone→Speaker→Phone→PC; button disabled during run.

4\. Toggle Naive, re-run → visibly jittery handoffs (the industry's flapping problem); toggle Hysteresis → stable. This before/after is the demo's money shot.

5\. Click the position track → dot springs to clicked spot, signals update live.

