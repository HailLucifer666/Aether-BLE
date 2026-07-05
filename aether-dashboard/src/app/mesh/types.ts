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
