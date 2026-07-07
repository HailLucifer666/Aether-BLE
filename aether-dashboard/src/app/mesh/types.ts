/**
 * Locked wire schema published by the server-side mesh aggregator
 * (ws://127.0.0.1:8766). Do not rename/reshape fields — the backend is
 * building to this exact contract in parallel.
 */

export type MeshConnectionState = "connecting" | "live" | "offline";

export type WakeResultOutcome = "ACCEPTED" | "SUPPRESSED";

export interface ScannerEntry {
  id: string;
  rssi: number | null;
  smoothedRssi: number | null;
  lastSeenMs: number | null;
  present: boolean;
}

export interface HandoffInfo {
  from: string;
  to: string;
  atTick: number;
  ts: string;
}

export interface WakeResult {
  id: string;
  outcome: WakeResultOutcome;
}

export interface WakeOutcome {
  requestedAtTick: number;
  ts: string;
  owner: string | null;
  results: WakeResult[];
}

export interface ElectionMessage {
  type: "election";
  owner: string | null;
  tick: number;
  ts: string;
  scanners: ScannerEntry[];
  lastHandoff: HandoffInfo | null;
  wakeOutcome: WakeOutcome | null;
}

export interface WakeRequestMessage {
  type: "wake";
  requestId: string;
}

/** Client-side handoff-log entry rendered by MeshView; mirrors the shape of
 * page.tsx's own HandoffEvent so the shared visual style lines up, but is
 * always derived from the aggregator's lastHandoff — never computed locally. */
export interface MeshHandoffLogEntry {
  id: number;
  from: string;
  to: string;
  atTick: number;
  time: string;
}

// ---------------------------------------------------------------------------
// Phase 3 — portable conversation state.
//
// Added as new message types alongside ElectionMessage/WakeRequestMessage;
// the existing ElectionMessage contract stays frozen and untouched. The
// dashboard dispatches inbound messages by `type` in useElectionSocket.
// ---------------------------------------------------------------------------

export type HandoffPhase = "IDLE" | "PREPARE" | "TRANSFER" | "CONFIRM" | "RELEASE";

export interface TranscriptEntry {
  id: number;
  scanner: string;
  role: string;
  text: string;
  ts: string;
}

export interface ConversationUtterance {
  text: string;
  /** data: URL (`data:audio/mp3;base64,...`), or null when no audio was generated. */
  audioBase64: string | null;
  durationMs: number;
  offsetMs: number;
  isSynthetic: boolean;
}

export interface ConversationEvent {
  phase: HandoffPhase;
  fromScanner: string | null;
  toScanner: string | null;
  atTick: number;
}

export interface ConversationMessage {
  type: "conversation";
  transcript: TranscriptEntry[];
  utterance: ConversationUtterance | null;
  speakingScanner: string | null;
  phase: HandoffPhase;
  phaseFrom: string | null;
  phaseTo: string | null;
  /** One-shot phase-transition notification (mirrors wakeOutcome's semantics). */
  conversationEvent: ConversationEvent | null;
}

export interface SayMessage {
  type: "say";
  text: string;
  requestId: string;
}

// ---------------------------------------------------------------------------
// Phase 4 — tiered sensing: BLE + near-ultrasound chirp tie-breaker.
//
// Added as a new message type alongside ElectionMessage/ConversationMessage;
// the existing contracts stay frozen and untouched. The dashboard dispatches
// inbound messages by `type` in useElectionSocket. A "ranging" message is
// only broadcast when tier 2 has been invoked (a contested election), so the
// wire stays quiet in the common uncontested case.
// ---------------------------------------------------------------------------

/** Machine-readable tag describing how the latest owner decision was reached. */
export type FusionReason =
  | "ble-only"
  | "chirp-confirmed"
  | "chirp-resolved-tie"
  | "chirp-room-containment";

export interface ContestInfo {
  incumbentId: string;
  challengerId: string;
  incumbentRssi: number;
  challengerRssi: number;
  atTick: number;
}

export interface ChirpMeasurement {
  scannerId: string;
  /** One-way time-of-flight in microseconds. */
  tofUs: number;
  /** tofUs converted to meters via the speed of sound. */
  distanceM: number;
}

export interface ChirpInfo {
  measurements: ChirpMeasurement[];
  /** Closest device among those that heard the chirp, or null if none heard. */
  winnerId: string | null;
  /** True iff BOTH contest parties appear in measurements (the room-containment bit). */
  sameRoom: boolean;
  resolvedTick: number;
}

/** One-shot chirp-round notification (mirrors wakeOutcome/conversationEvent
 * semantics): attached to exactly one broadcast, then cleared. The dashboard
 * uses it to fire the "chirp ping" animation. */
export interface RangingEvent {
  /** Always "CHIRP" today; a string tag kept for forward-compat with future
   * ranging sub-events (e.g. "ESCALATE"). */
  phase: string;
  contestIncumbent: string;
  contestChallenger: string;
  winnerId: string | null;
  sameRoom: boolean;
  atTick: number;
}

export interface RangingMessage {
  type: "ranging";
  /** Current contest state, or null when the election is not contested. */
  contest: ContestInfo | null;
  /** Most-recent chirp resolution still considered relevant, or null. */
  chirp: ChirpInfo | null;
  fusionReason: FusionReason;
  /** One-shot; null on every broadcast except the one carrying a fresh chirp. */
  rangingEvent: RangingEvent | null;
}

// ---------------------------------------------------------------------------
// Phase 10 — spatial fusion: 2-D position, device placement, calibration,
// and live tuning.
//
// Added as new message types alongside the existing Election/Conversation/
// Ranging contracts, which stay frozen and untouched. `position` is
// server->client only, broadcast once per tracked user with an active fusion
// track (omitted entirely from the wire when no track exists yet). The other
// three are client->server only, sent by the Spatial/Setup/Signal Lab views.
// ---------------------------------------------------------------------------

/** Server->client: one broadcast per tracked user with an active fusion
 * track. Omitted entirely when no track exists yet (fresh install / nothing
 * placed on the floor plan) - the dashboard must render correctly with zero
 * of these ever arriving. */
export interface PositionMessage {
  type: "position";
  userId: string;
  x: number;
  y: number;
  uncertaintyRadiusM: number;
}

/** Client->server: sent when the user drags a scanner icon on the floor
 * plan (on pointerup, not every pointermove tick). */
export interface PlaceDeviceMessage {
  type: "placeDevice";
  scannerId: string;
  x: number;
  y: number;
}

/** Client->server: sent from the setup wizard's numeric calibration inputs
 * (on submit/blur, not on every keystroke). */
export interface SetCalibrationMessage {
  type: "setCalibration";
  scannerId: string;
  rssiAt1m: number;
  pathLossExponent: number;
}

/** Client->server: sent from Signal Lab's tuning sliders (debounced). */
export interface SetTuningMessage {
  type: "setTuning";
  hysteresisDb: number;
  consecutiveTicks: number;
  contestMarginDb: number;
}
