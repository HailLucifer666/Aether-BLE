# AETHER PROTOCOL — DEMO SPEC v0.1
## For AI Code Generation

---

### 1. PROJECT OVERVIEW

Build a web dashboard that demonstrates cross-device AI arbitration
using BLE proximity simulation. No real BLE hardware needed.
Pure frontend demo in Next.js + React + Tailwind + Framer Motion.

---

### 2. CORE CONCEPT

User carries a BLE beacon (simulated as a phone).
3 devices in a room detect signal strength.
Closest device "owns" the AI conversation.
When user moves, conversation hands off to new closest device.

---

### 3. SCREEN: MAIN DASHBOARD

Layout: Single page, dark theme (slate-950 bg), max-w-6xl centered.

Sections:

```
┌─────────────────────────────────────────┐
│  HEADER: "Aether Protocol v0.1"         │
│  Sub: "Cross-Device AI Arbitration"     │
├─────────────────────────────────────────┤
│                                         │
│  ROOM MAP (60% width)                   │
│  - 3 device icons: PC(-2m), Phone(0m),  │
│    Speaker(3m)                          │
│  - User dot (amber) moves on click      │
│  - Active device gets pulsing border    │
│                                         │
├─────────────────────────────────────────┤
│  SIGNAL PANEL (40% width)               │
│  - 3 bars, real-time RSSI               │
│  - Active = cyan highlight              │
│  - Distance shown below each            │
│                                         │
├─────────────────────────────────────────┤
│  HANDOFF LOG (full width)               │
│  - Timestamped events: PC→Phone→Speaker │
│  - Max 20 entries, scrollable           │
│  - Animate new entries from left        │
│                                         │
└─────────────────────────────────────────┘
```

---

### 4. DATA MODEL

```typescript
interface Device {
  name: string;        // "PC" | "Phone" | "Speaker"
  position: number;    // meters from origin (-2, 0, 3)
  baseRssi: number;    // signal at 1m distance (-45, -55, -50)
  icon: string;        // lucide icon name
  color: string;       // tailwind class for bar color
}

interface HandoffEvent {
  from: string;
  to: string;
  rssi: number;
  time: string;        // HH:MM:SS format
}

// State
userPosition: number   // -2.5 to 3.5 meters
activeDevice: string | null
handoffs: HandoffEvent[]
isSimulating: boolean
```

---

### 5. SIMULATION LOGIC

```typescript
// RSSI calculation (path loss + noise)
function calculateRssi(device: Device, userPos: number): number {
  const distance = Math.abs(userPos - device.position);
  const noise = (Math.random() - 0.5) * 8;  // ±4 dBm jitter
  return device.baseRssi - (distance * 6) + noise;
}

// Signal bars (0-5)
function getBars(rssi: number): number {
  return Math.max(0, Math.min(5, Math.floor((rssi + 90) / 8)));
}

// Handoff trigger
// If best device changes from previous active:
//   1. Log handoff event
//   2. Update activeDevice
//   3. Animate pulsing border on new device
```

---

### 6. PREDEFINED SIMULATION PATH

Auto-play sequence when "Run Simulation" clicked:

```
[0, 0.5, 1.0, 2.0, 2.5, 3.0, 2.0, 1.0, 0, -1.0, -2.0, -2.5, -2.0]
// Start at Phone → walk to Speaker → back to Phone → to PC
// Total: 13 positions, 1.2s each = ~15 seconds
```

---

### 7. INTERACTIONS

| Action | Result |
|--------|--------|
| Click "Run Simulation" | Auto-plays path, disables button |
| Click bottom bar | Moves user dot to clicked position |
| User dot animation | Spring physics, 0.5s duration |
| Device activation | Scale 1.0→1.2, white border pulse |
| New handoff | Log entry slides in from left |

---

### 8. TECH STACK

- Next.js 15 (App Router)
- React 19
- TypeScript
- Tailwind CSS
- Framer Motion (animations)
- Lucide React (icons)

NO backend. NO API. NO database. Pure client-side.

---

### 9. FILE STRUCTURE

```
aether-dashboard/
├── package.json
├── next.config.js
├── tailwind.config.ts
├── tsconfig.json
└── src/
    └── app/
        ├── layout.tsx      ← Root layout, metadata
        ├── globals.css     ← Tailwind imports, custom scrollbar
        └── page.tsx        ← ALL logic, ALL UI, ALL components
                            ← Single 200-line file. No subfolders.
```

---

### 10. CONSTRAINTS FOR AI

- ONE file for all logic: page.tsx
- NO separate component files
- NO custom hooks
- NO context providers
- Use useState, useCallback, useEffect only
- Animations via Framer Motion motion.div only
- Icons via lucide-react only
- Dark theme: bg-slate-950, text-slate-100
- Accent: cyan-400/500, amber-500 for user

---

### 11. OUTPUT EXPECTATION

Generate complete, runnable page.tsx that:

- Renders all sections
- Runs simulation on button click
- Handles manual user positioning
- Logs handoffs with timestamps
- Uses Framer Motion for all animations
- Is fully typed (no any)
- Works when pasted into fresh Next.js project
