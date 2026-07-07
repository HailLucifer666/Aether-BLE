"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ChirpInfo,
  ContestInfo,
  ConversationMessage,
  ConversationUtterance,
  ElectionMessage,
  FusionReason,
  HandoffPhase,
  MeshConnectionState,
  MeshHandoffLogEntry,
  PositionMessage,
  RangingEvent,
  RangingMessage,
  ScannerEntry,
  TranscriptEntry,
  WakeOutcome,
} from "./types";

const MESH_WS_URL = "ws://127.0.0.1:8766";
const MESH_RETRY_MS = 2000;
const MAX_MESH_LOG_ENTRIES = 20;
const WAKE_OUTCOME_DISPLAY_MS = 5000;
// How long the Phase 4 chirp-ping animation stays "armed" after a ranging
// event arrives. Matches roughly one chirp round-trip in the dashboard's
// perception; the next chirp re-arms it.
const RANGING_EVENT_DISPLAY_MS = 2500;

export interface UseElectionSocketResult {
  connection: MeshConnectionState;
  owner: string | null;
  tick: number | null;
  scanners: ScannerEntry[];
  handoffLog: MeshHandoffLogEntry[];
  wakeOutcome: WakeOutcome | null;
  sendWake: () => void;
  // Phase 3: portable conversation state.
  transcript: TranscriptEntry[];
  utterance: ConversationUtterance | null;
  speakingScanner: string | null;
  handoffPhase: HandoffPhase;
  phaseFrom: string | null;
  phaseTo: string | null;
  sendSay: (text: string) => void;
  /** MeshView calls this with its hidden <audio> element so the hook can
   * drive playback (load, play, pause-on-TRANSFER, resume-on-RELEASE). */
  registerAudioElement: (el: HTMLAudioElement | null) => void;
  // Phase 4: tiered ranging. contest is non-null while a photo-finish is
  // live; chirp is the most-recent chirp resolution; fusionReason labels
  // how the latest owner decision was reached; rangingEvent fires the
  // one-shot "chirp ping" animation when a fresh chirp arrives.
  contest: ContestInfo | null;
  chirp: ChirpInfo | null;
  fusionReason: FusionReason;
  rangingEvent: RangingEvent | null;
  // Phase 10: spatial fusion. positions holds the latest `position` message
  // per userId (server-authoritative; never computed locally). The three
  // send functions mirror sendWake/sendSay's exact style.
  positions: Map<string, { x: number; y: number; uncertaintyRadiusM: number }>;
  sendPlaceDevice: (scannerId: string, x: number, y: number) => void;
  sendSetCalibration: (scannerId: string, rssiAt1m: number, pathLossExponent: number) => void;
  sendSetTuning: (hysteresisDb: number, consecutiveTicks: number, contestMarginDb: number) => void;
}

function randomRequestId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/**
 * Pure-viewer WebSocket client for the mesh aggregator (ws://127.0.0.1:8766).
 * Never arbitrates ownership locally — every field rendered comes straight
 * from the most recent `election` message. Auto-reconnects every 2s.
 *
 * Phase 3: also consumes `conversation` messages and drives a hidden <audio>
 * element. On an utterance arriving with audio, it loads + plays. On a
 * TRANSFER conversation event it pauses and records the playhead; on RELEASE
 * it seeks back to that offset and resumes — so the user hears the sentence
 * pause ~400ms mid-word as ownership migrates, then continue under the new
 * owner. The hook never decides ownership; it only obeys the events.
 */
export function useElectionSocket(enabled: boolean): UseElectionSocketResult {
  const [connection, setConnection] = useState<MeshConnectionState>("connecting");
  const [owner, setOwner] = useState<string | null>(null);
  const [tick, setTick] = useState<number | null>(null);
  const [scanners, setScanners] = useState<ScannerEntry[]>([]);
  const [handoffLog, setHandoffLog] = useState<MeshHandoffLogEntry[]>([]);
  const [wakeOutcome, setWakeOutcome] = useState<WakeOutcome | null>(null);

  // Phase 3 conversation state.
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [utterance, setUtterance] = useState<ConversationUtterance | null>(null);
  const [speakingScanner, setSpeakingScanner] = useState<string | null>(null);
  const [handoffPhase, setHandoffPhase] = useState<HandoffPhase>("IDLE");
  const [phaseFrom, setPhaseFrom] = useState<string | null>(null);
  const [phaseTo, setPhaseTo] = useState<string | null>(null);

  // Phase 4 ranging state. rangingEvent is one-shot: set when a fresh chirp
  // arrives, auto-cleared after a short display window so the ping animation
  // plays once per chirp round.
  const [contest, setContest] = useState<ContestInfo | null>(null);
  const [chirp, setChirp] = useState<ChirpInfo | null>(null);
  const [fusionReason, setFusionReason] = useState<FusionReason>("ble-only");
  const [rangingEvent, setRangingEvent] = useState<RangingEvent | null>(null);
  const rangingEventTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Phase 10 spatial fusion: latest position per userId. A plain object map
  // (not a Map instance) so React's setState reference-equality check works
  // the same way as the rest of this hook's state; converted to a Map only
  // at the return boundary for callers that prefer Map ergonomics.
  const [positions, setPositions] = useState<
    Map<string, { x: number; y: number; uncertaintyRadiusM: number }>
  >(new Map());

  const wsRef = useRef<WebSocket | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wakeOutcomeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks the atTick of the last lastHandoff we've already logged, so a
  // repeated election message carrying the same lastHandoff doesn't append
  // a duplicate log entry. null means "nothing logged yet".
  const lastLoggedHandoffTickRef = useRef<number | null>(null);
  const logIdRef = useRef<number>(0);

  // Phase 3: audio + conversation refs.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // The data URL currently loaded into the audio element, so we only reload
  // when a NEW utterance arrives (not on every broadcast of the same one).
  const loadedAudioSrcRef = useRef<string | null>(null);
  // Playhead offset (seconds) captured at TRANSFER so RELEASE can seek back
  // to the same word and resume — the "finishes its sentence" effect.
  const resumeOffsetRef = useRef<number>(0);

  const registerAudioElement = useCallback((el: HTMLAudioElement | null) => {
    audioRef.current = el;
    if (el === null) {
      loadedAudioSrcRef.current = null;
    }
  }, []);

  const handleElectionMessage = useCallback((msg: ElectionMessage) => {
    setOwner(msg.owner);
    setTick(msg.tick);
    setScanners(msg.scanners);

    if (msg.lastHandoff !== null && msg.lastHandoff.atTick !== lastLoggedHandoffTickRef.current) {
      lastLoggedHandoffTickRef.current = msg.lastHandoff.atTick;
      const entry: MeshHandoffLogEntry = {
        id: logIdRef.current++,
        from: msg.lastHandoff.from,
        to: msg.lastHandoff.to,
        atTick: msg.lastHandoff.atTick,
        time: msg.lastHandoff.ts,
      };
      setHandoffLog((prev) => [entry, ...prev].slice(0, MAX_MESH_LOG_ENTRIES));
    }

    if (msg.wakeOutcome !== null) {
      setWakeOutcome(msg.wakeOutcome);
      if (wakeOutcomeTimerRef.current !== null) {
        clearTimeout(wakeOutcomeTimerRef.current);
      }
      wakeOutcomeTimerRef.current = setTimeout(() => {
        setWakeOutcome(null);
        wakeOutcomeTimerRef.current = null;
      }, WAKE_OUTCOME_DISPLAY_MS);
    }
  }, []);

  const handleConversationMessage = useCallback((msg: ConversationMessage) => {
    setTranscript(msg.transcript);
    setUtterance(msg.utterance);
    setSpeakingScanner(msg.speakingScanner);
    setHandoffPhase(msg.phase);
    setPhaseFrom(msg.phaseFrom);
    setPhaseTo(msg.phaseTo);

    const audio = audioRef.current;

    // If a NEW utterance arrived with audio we haven't loaded yet, load +
    // play it. Synthetic utterances (no audio) just animate the progress bar.
    if (msg.utterance !== null && msg.utterance.audioBase64 !== null) {
      if (audio !== null && loadedAudioSrcRef.current !== msg.utterance.audioBase64) {
        loadedAudioSrcRef.current = msg.utterance.audioBase64;
        audio.src = msg.utterance.audioBase64;
        resumeOffsetRef.current = 0;
        // If we're mid-handoff (phase != IDLE) don't autoplay - the FSM will
        // resume via RELEASE. This is rare for a brand-new utterance but
        // covers the edge case where a say races an in-flight handoff.
        if (msg.phase === "IDLE") {
          audio.currentTime = 0;
          void audio.play().catch(() => {
            // Autoplay can be blocked before a user gesture; the user's
            // click on Send counts as a gesture so this usually succeeds.
            // If it fails, the UI still animates; audio is best-effort.
          });
        }
      }
    } else if (msg.utterance === null && audio !== null) {
      // Utterance finished - stop and clear audio.
      if (!audio.paused) {
        audio.pause();
      }
      loadedAudioSrcRef.current = null;
    }

    // React to the one-shot conversationEvent (phase transition). These arrive
    // on exactly one broadcast each; we drive audio pause/resume off them.
    const event = msg.conversationEvent;
    if (event !== null && audio !== null) {
      if (event.phase === "TRANSFER") {
        // Ownership is migrating: pause audio at the current word and record
        // the playhead so RELEASE can seek back to it.
        if (!audio.paused) {
          resumeOffsetRef.current = audio.currentTime;
          audio.pause();
        }
      } else if (event.phase === "RELEASE") {
        // Migration complete: resume from the captured offset, now attributed
        // (via speakingScanner) to the new owner.
        audio.currentTime = resumeOffsetRef.current;
        void audio.play().catch(() => {
          // Best-effort; some browsers block resume without a gesture.
        });
      }
    }
  }, []);

  const handleRangingMessage = useCallback((msg: RangingMessage) => {
    setContest(msg.contest);
    setChirp(msg.chirp);
    setFusionReason(msg.fusionReason);

    // One-shot rangingEvent: latch it on arrival, auto-clear after a short
    // window so the chirp-ping animation plays once per chirp round. The
    // server already sends it on exactly one broadcast; this timer is a
    // belt-and-braces guarantee the UI doesn't get stuck mid-animation if a
    // later ranging broadcast happens to arrive before the next real chirp.
    if (msg.rangingEvent !== null) {
      setRangingEvent(msg.rangingEvent);
      if (rangingEventTimerRef.current !== null) {
        clearTimeout(rangingEventTimerRef.current);
      }
      rangingEventTimerRef.current = setTimeout(() => {
        setRangingEvent(null);
        rangingEventTimerRef.current = null;
      }, RANGING_EVENT_DISPLAY_MS);
    }
  }, []);

  const handlePositionMessage = useCallback((msg: PositionMessage) => {
    setPositions((prev) => {
      const next = new Map(prev);
      next.set(msg.userId, {
        x: msg.x,
        y: msg.y,
        uncertaintyRadiusM: msg.uncertaintyRadiusM,
      });
      return next;
    });
  }, []);

  useEffect(() => {
    if (!enabled) {
      if (retryTimerRef.current !== null) {
        clearInterval(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      if (wsRef.current !== null) {
        wsRef.current.close();
        wsRef.current = null;
      }
      return;
    }

    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      setConnection((prev) => (prev === "live" ? prev : "connecting"));
      const socket = new WebSocket(MESH_WS_URL);
      wsRef.current = socket;

      socket.onopen = () => {
        if (cancelled) return;
        setConnection("live");
      };

      socket.onmessage = (event) => {
        if (cancelled) return;
        let parsed: ElectionMessage | ConversationMessage | RangingMessage | PositionMessage;
        try {
          parsed = JSON.parse(event.data as string) as
            | ElectionMessage
            | ConversationMessage
            | RangingMessage
            | PositionMessage;
        } catch {
          return;
        }
        if (parsed.type === "election") {
          handleElectionMessage(parsed);
        } else if (parsed.type === "conversation") {
          handleConversationMessage(parsed);
        } else if (parsed.type === "ranging") {
          handleRangingMessage(parsed);
        } else if (parsed.type === "position") {
          handlePositionMessage(parsed);
        }
      };

      const handleDisconnect = () => {
        if (cancelled) return;
        setConnection("offline");
        wsRef.current = null;
      };
      socket.onclose = handleDisconnect;
      socket.onerror = handleDisconnect;
    };

    connect();
    retryTimerRef.current = setInterval(() => {
      if (wsRef.current === null || wsRef.current.readyState === WebSocket.CLOSED) {
        connect();
      }
    }, MESH_RETRY_MS);

    return () => {
      cancelled = true;
      if (retryTimerRef.current !== null) {
        clearInterval(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      if (wsRef.current !== null) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [
    enabled,
    handleElectionMessage,
    handleConversationMessage,
    handleRangingMessage,
    handlePositionMessage,
  ]);

  // Reset all mesh-derived state whenever the socket is disabled, so
  // switching away and back to Mesh mode never shows stale data from a
  // previous session.
  useEffect(() => {
    if (enabled) return;
    setConnection("connecting");
    setOwner(null);
    setTick(null);
    setScanners([]);
    setHandoffLog([]);
    setWakeOutcome(null);
    // Phase 3 resets.
    setTranscript([]);
    setUtterance(null);
    setSpeakingScanner(null);
    setHandoffPhase("IDLE");
    setPhaseFrom(null);
    setPhaseTo(null);
    // Phase 4 resets.
    setContest(null);
    setChirp(null);
    setFusionReason("ble-only");
    setRangingEvent(null);
    setPositions(new Map());
    lastLoggedHandoffTickRef.current = null;
    loadedAudioSrcRef.current = null;
    resumeOffsetRef.current = 0;
    const audio = audioRef.current;
    if (audio !== null && !audio.paused) {
      audio.pause();
    }
    if (wakeOutcomeTimerRef.current !== null) {
      clearTimeout(wakeOutcomeTimerRef.current);
      wakeOutcomeTimerRef.current = null;
    }
    if (rangingEventTimerRef.current !== null) {
      clearTimeout(rangingEventTimerRef.current);
      rangingEventTimerRef.current = null;
    }
  }, [enabled]);

  useEffect(() => {
    return () => {
      if (wakeOutcomeTimerRef.current !== null) {
        clearTimeout(wakeOutcomeTimerRef.current);
      }
      if (rangingEventTimerRef.current !== null) {
        clearTimeout(rangingEventTimerRef.current);
      }
    };
  }, []);

  const sendWake = useCallback(() => {
    const socket = wsRef.current;
    if (socket === null || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "wake", requestId: randomRequestId() }));
  }, []);

  const sendSay = useCallback((text: string) => {
    const socket = wsRef.current;
    if (socket === null || socket.readyState !== WebSocket.OPEN) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    socket.send(JSON.stringify({ type: "say", text: trimmed, requestId: randomRequestId() }));
  }, []);

  // Phase 10 send functions, following sendWake/sendSay's exact style: a
  // no-op when the socket isn't open, otherwise a single JSON.stringify send.
  // The server re-validates and silently drops out-of-range values, but
  // callers (Spatial/Setup/Signal Lab) still clamp client-side so the UI
  // itself never sends nonsense.
  const sendPlaceDevice = useCallback((scannerId: string, x: number, y: number) => {
    const socket = wsRef.current;
    if (socket === null || socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "placeDevice", scannerId, x, y }));
  }, []);

  const sendSetCalibration = useCallback(
    (scannerId: string, rssiAt1m: number, pathLossExponent: number) => {
      const socket = wsRef.current;
      if (socket === null || socket.readyState !== WebSocket.OPEN) return;
      socket.send(
        JSON.stringify({ type: "setCalibration", scannerId, rssiAt1m, pathLossExponent })
      );
    },
    []
  );

  const sendSetTuning = useCallback(
    (hysteresisDb: number, consecutiveTicks: number, contestMarginDb: number) => {
      const socket = wsRef.current;
      if (socket === null || socket.readyState !== WebSocket.OPEN) return;
      socket.send(
        JSON.stringify({ type: "setTuning", hysteresisDb, consecutiveTicks, contestMarginDb })
      );
    },
    []
  );

  return {
    connection,
    owner,
    tick,
    scanners,
    handoffLog,
    wakeOutcome,
    sendWake,
    transcript,
    utterance,
    speakingScanner,
    handoffPhase,
    phaseFrom,
    phaseTo,
    sendSay,
    registerAudioElement,
    contest,
    chirp,
    fusionReason,
    rangingEvent,
    positions,
    sendPlaceDevice,
    sendSetCalibration,
    sendSetTuning,
  };
}
