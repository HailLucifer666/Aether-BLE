"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ConversationMessage,
  ConversationUtterance,
  ElectionMessage,
  HandoffPhase,
  MeshConnectionState,
  MeshHandoffLogEntry,
  ScannerEntry,
  TranscriptEntry,
  WakeOutcome,
} from "./types";

const MESH_WS_URL = "ws://127.0.0.1:8766";
const MESH_RETRY_MS = 2000;
const MAX_MESH_LOG_ENTRIES = 20;
const WAKE_OUTCOME_DISPLAY_MS = 5000;

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
        let parsed: ElectionMessage | ConversationMessage;
        try {
          parsed = JSON.parse(event.data as string) as ElectionMessage | ConversationMessage;
        } catch {
          return;
        }
        if (parsed.type === "election") {
          handleElectionMessage(parsed);
        } else if (parsed.type === "conversation") {
          handleConversationMessage(parsed);
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
  }, [enabled, handleElectionMessage, handleConversationMessage]);

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
  }, [enabled]);

  useEffect(() => {
    return () => {
      if (wakeOutcomeTimerRef.current !== null) {
        clearTimeout(wakeOutcomeTimerRef.current);
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
  };
}
