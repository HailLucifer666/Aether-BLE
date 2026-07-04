# AETHER PROTOCOL — COMPLETE HANDOFF DOCUMENT

## 1. PROBLEM DEFINITION

**The "Google Problem"**
When you say "Hey Google" or "Alexa" or "Siri," multiple devices activate simultaneously (phone, smart speaker, watch, earbuds). The user has no control over which device responds. The arbitration is cloud-based, opaque, and frequently wrong.

**Why Existing Solutions Fail**
- Google/Apple/Amazon use cloud-based arbitration after the fact — devices independently detect wake words, then a central server guesses which should win
- No user ownership — the AI lives in their cloud, not yours
- No cross-platform interoperability — Apple devices only talk to Apple, Google to Google
- No persistent identity — your AI doesn't follow you, it lives on their servers

**The Core Insight**
The entity (your AI) should live on YOUR server. Devices are just dumb microphones/speakers. Proximity determines which device is active. The user owns everything.

---

## 2. ARCHITECTURE EVOLUTION

### Phase 1: Concept (JARVIS Inspiration)
- Persistent AI entity that follows user across devices
- Voice-first, always available
- Cross-platform: phone, PC, tablet, headphones, smart speaker
- User-owned, not corporate-owned

### Phase 2: Hardware Exploration (Abandoned)
- **Smart ring idea** — BLE beacon in ring form factor for proximity detection
- **Rejected because:** custom PCB, firmware, manufacturing, FCC certification = $50K+ and 12+ months
- **Pivot:** Use existing phone as BLE beacon instead

### Phase 3: Protocol Design (Current)

```
┌─────────────┐     BLE Beacon     ┌─────────────┐
│   Phone     │ ─────────────────→ │  PC Client  │
│  (User)     │  (RSSI signal)     │  (Scanner)  │
└─────────────┘                    └──────┬──────┘
                                          │
                                          ↓
                                   ┌─────────────┐
                                   │ Aether      │
                                   │ Arbitrator  │
                                   │ (Daemon)    │
                                   └──────┬──────┘
                                          │
                                          ↓
                                   ┌─────────────┐
                                   │  AI Model   │
                                   │ (Local/Your)│
                                   └─────────────┘
```

**Key Innovation:** The arbitrator uses BLE signal strength (RSSI) to determine which device is closest to the user. The closest device "wins" and receives the AI audio stream.

---

## 3. TECHNICAL IMPLEMENTATION

### 3.1 BLE Beacon (Phone Side)
- **App:** nRF Connect (free, by Nordic Semiconductor)
- **Configuration:**
  - Device Name: `AetherUser1`
  - TX Power: 0 dBm (maximum without error on OnePlus 7T)
  - Advertising Interval: 400 (250ms)
  - Duration: Until manually turned off
  - No maximum events
- **Status:** Successfully configured and broadcasting

### 3.2 BLE Scanner (PC Side)
- **Library:** `bleak` (Python, cross-platform)
- **Issue:** Windows Bluetooth stack failed to detect advertisement
  - Multiple devices in Bluetooth list causing interference
  - PowerShell `Disable-PnpDevice` failed with generic errors
  - Windows BLE stack historically unreliable for passive scanning
- **Workaround:** Build simulator first, add real BLE later

### 3.3 Simulator (Built & Working)
**Purpose:** Validate arbitration logic without hardware debugging

**Python Implementation:**

```python
class FakeDevice:
    def __init__(self, name, base_rssi, position):
        self.name = name
        self.base_rssi = base_rssi
        self.position = position

    def read_signal(self, user_position):
        distance = abs(user_position - self.position)
        rssi = self.base_rssi - (distance * 6) + random.randint(-4, 4)
        return rssi, distance

class AetherArbitrator:
    def __init__(self):
        self.devices = {}
        self.active_device = None
        self.conversation_history = []

    def update_user_position(self, new_position):
        # Calculate all signal readings
        # Find best device
        # If different from active, log handoff
        # Update active_device
```

**Simulation Results:**
- 3 devices: PC(-2m), Phone(0m), Speaker(3m)
- 13-position walkthrough
- Automatic handoff detection
- Signal strength visualization with bars
- Conversation history logging

**Output:** Successfully demonstrated handoff logic working correctly

---

## 4. DASHBOARD DESIGN (Next.js)

### 4.1 UI Layout

```
┌─────────────────────────────────────────┐
│  HEADER: "Aether Protocol v0.1"         │
│  Sub: "Cross-Device AI Arbitration"     │
├─────────────────────────────────────────┤
│                                         │
│  ROOM MAP (60% width)                   │
│  - 3 device icons positioned by meters  │
│  - User dot (amber) animates on move    │
│  - Active device: pulsing white border  │
│  - Click map to move user manually      │
│                                         │
├─────────────────────────────────────────┤
│  SIGNAL PANEL (40% width)               │
│  - 5-bar signal strength per device     │
│  - Active device highlighted in cyan    │
│  - Distance in meters shown             │
│  - Hysteresis toggle (ON/OFF)           │
│                                         │
├─────────────────────────────────────────┤
│  HANDOFF LOG (full width)               │
│  - Timestamped: 14:32:15 PC → Phone     │
│  - Animated entry (slide from left)     │
│  - Max 20 entries, scrollable           │
│                                         │
└─────────────────────────────────────────┘
```

### 4.2 Hysteresis Logic (Critical Feature)
**Problem:** Without hysteresis, ±4 dBm noise causes rapid handoff flapping between devices with similar signal strength

**Solution:**
- Challenger must beat active device by 5 dBm
- AND win for 2 consecutive readings
- Prevents noise-induced flickering

**Implementation:**

```typescript
const HYSTERESIS_ENABLED = true;
const HYSTERESIS_THRESHOLD_DB = 5;
const HYSTERESIS_CONSECUTIVE = 2;

// State tracking
let challenger: string | null = null;
let consecutiveWins = 0;

function shouldHandoff(current, currentRssi, best, bestRssi) {
  if (!hysteresis) return true; // instant switch

  if (best === current) {
    challenger = null;
    consecutiveWins = 0;
    return false;
  }

  if (challenger === best) {
    consecutiveWins++;
    if (consecutiveWins >= 2 && bestRssi > currentRssi + 5) {
      return true; // HANDOFF
    }
  } else {
    challenger = best;
    consecutiveWins = 1;
  }
  return false;
}
```

**UI Toggle:** Button shows "Hysteresis: ON/OFF", toggles between modes

### 4.3 Tech Stack
- Next.js 15 (App Router)
- React 19
- TypeScript
- Tailwind CSS
- Framer Motion (animations)
- Lucide React (icons)

**Constraint:** Single file (`page.tsx`), no subcomponents, no backend

---

## 5. FILE STRUCTURE

```
aether-dashboard/
├── README.md
├── PRD.md
├── AETHER_SPEC.md       ← AI prompt document
├── package.json
├── next.config.js
├── tailwind.config.ts
└── src/
    └── app/
        ├── layout.tsx      ← Root layout, metadata
        ├── globals.css     ← Tailwind imports
        └── page.tsx        ← ALL logic + UI (single file)
```

---

## 6. RESEARCH CONDUCTED

### 6.1 Existing Projects (Analyzed)

| Project | What It Is | Why It Doesn't Solve the Problem |
|---------|-----------|----------------------------------|
| Open Jarvis | Python voice assistant | Single device, no cross-device handoff |
| Paperclip | Desktop AI wrapper | Single device, no protocol |
| OpenHumans | Data platform | Not voice/AI focused |
| Vella | Web-based AI assistant | Browser-only, no device arbitration |
| D Pet Companion | AI pet game | Entertainment, not infrastructure |
| Odysseus | Agent framework | Task execution, not device coordination |

**Gap:** None implement cross-device proximity arbitration with user-owned entities

### 6.2 Corporate Solutions (Analyzed)

| Company | Approach | Limitation |
|---------|----------|------------|
| Google | Cloud arbitration after wake word | Opaque, wrong device often wins, privacy issues |
| Apple | Device-type priority (HomePod over iPhone) | Not proximity-based, ecosystem lock-in |
| Amazon | Echo devices coordinate via cloud | Same as Google, no local arbitration |

**Common flaw:** All use cloud-based, post-hoc arbitration. None use real-time BLE proximity with local decision-making.

### 6.3 Protocol Standards (Researched)
- **A2A (Agent-to-Agent Protocol)** by Google: Task delegation between agents, not device arbitration
- **MCP (Model Context Protocol)** by Anthropic: Context sharing, not proximity routing
- **IETF Draft:** Cross-device AI communication for network devices, not consumer hardware

**Gap:** No open standard for BLE-based device arbitration

---

## 7. WHAT WAS BUILT

| Component | Status | Evidence |
|-----------|--------|----------|
| BLE beacon configuration | ✅ Working | nRF Connect screenshots, -1 dBm broadcasting |
| Python simulator | ✅ Working | Terminal output showing handoffs |
| Arbitration logic | ✅ Validated | 13-position simulation, correct handoffs |
| Hysteresis concept | ✅ Designed | Prevents flapping, 5dBm/2-reading threshold |
| Next.js dashboard | 🚧 Spec ready | Complete spec document generated |
| Real BLE integration | ❌ Blocked | Windows BLE stack unreliable |

---

## 8. WHAT WAS LEARNED

### Technical Lessons
1. **Windows BLE is unreliable for development** — use Linux/WSL2 or Raspberry Pi for real BLE integration
2. **Phone BLE advertising stops when screen locks** on some Android devices — need foreground service or wakelock
3. **RSSI is noisy** — ±4 dBm jitter requires hysteresis for stable arbitration
4. **OnePlus 7T TX power capped at 0 dBm** — higher values throw "out of range" error

### Strategic Lessons
1. **Hardware is a trap for non-hardware founders** — always start with software simulation
2. **The protocol is the product, not the device** — custom ring comes later, if ever
3. **Demo first, protocol second, ecosystem third** — don't build infrastructure before proving demand
4. **Open-source the protocol, license to hardware makers** — don't manufacture, standardize

---

## 9. NEXT STEPS (From Spec)

### Phase 1: Demo (This Weekend)
- Build Next.js dashboard with provided spec
- Record 60-second screen recording
- Post on GitHub, Hacker News, X, Reddit
- **Goal:** Validate interest, find co-founder

### Phase 2: Real BLE (If demo gets traction)
- Move to Linux/WSL2 or Raspberry Pi for reliable BLE
- Replace simulated signals with real `bleak` scanner
- Add voice wake word detection (whisper.cpp or similar)
- **Goal:** Working prototype with real hardware

### Phase 3: Protocol Standardization (If prototype works)
- Write formal protocol specification (RFC-style)
- Open-source reference implementation
- Recruit hardware partners (Oura, Apple, Samsung)
- **Goal:** Become the standard for cross-device AI arbitration

### Phase 4: Ecosystem (Year 2+)
- Custom hardware (ring, badge, wristband) — only if market validates
- Enterprise licensing to IoT manufacturers
- Multi-user, multi-room support
- **Goal:** Revenue through licensing and certification

---

## 10. KEY DECISIONS MADE

| Decision | Rationale |
|----------|-----------|
| Use phone as beacon, not custom ring | Avoid hardware manufacturing hell |
| Build simulator first | Validate logic before fighting Windows BLE |
| Single-file Next.js dashboard | Faster iteration, easier AI generation |
| Hysteresis toggle in UI | Demonstrate problem AND solution in one demo |
| Open-source from day one | Protocol adoption requires community |
| No backend for demo | Reduce complexity, focus on core UX |

---

## 11. RESOURCES & REFERENCES

- **nRF Connect app:** Nordic Semiconductor, free
- **bleak library:** Python BLE scanner, `pip install bleak`
- **Framer Motion:** Animation library, `npm install framer-motion`
- **Lucide React:** Icon library, `npm install lucide-react`
- **OnePlus 7T BLE behavior:** TX power capped at 0 dBm, advertising stops on screen lock

---

## 12. UNRESOLVED QUESTIONS

1. **Will Windows BLE ever work?** — Unknown, likely requires Linux/WSL2
2. **Can phone advertise continuously without draining battery?** — Needs testing with foreground service
3. **How to handle multiple users in same room?** — Voice signature + per-user beacon pairing (future)
4. **What's the business model?** — Protocol licensing, certification, enterprise support (not SaaS)
