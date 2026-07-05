"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Radio, Send, Waves, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getBars } from "@/lib/rssi";
import type { FusionReason, HandoffPhase } from "./types";
import type { UseElectionSocketResult } from "./useElectionSocket";
import type { MeshConnectionState } from "./types";

const MESH_WS_URL = "ws://127.0.0.1:8766";
const MESH_RETRY_MS = 2000;

const CONNECTION_LABEL: Record<MeshConnectionState, string> = {
  connecting: "CONNECTING",
  live: "LIVE",
  offline: "NOT CONNECTED",
};

const CONNECTION_PILL_CLASS: Record<MeshConnectionState, string> = {
  connecting: "border-slate-600 bg-slate-800 text-slate-300",
  live: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300",
  offline: "border-rose-500/60 bg-rose-500/10 text-rose-300",
};

// The four migration phases, in order. Used by the handoff-phase pill to
// show progress through PREPARE -> TRANSFER -> CONFIRM -> RELEASE.
const HANDOFF_PHASE_ORDER: HandoffPhase[] = ["PREPARE", "TRANSFER", "CONFIRM", "RELEASE"];

// Human-readable labels for how the latest owner decision was reached. The
// fusionReason arrives straight from the aggregator's ranging.fuse() and
// tells the dashboard which tier drove the decision (the Phase 4 value prop).
const FUSION_REASON_LABEL: Record<FusionReason, string> = {
  "ble-only": "BLE only",
  "chirp-confirmed": "Chirp confirmed BLE",
  "chirp-resolved-tie": "Chirp broke the tie",
  "chirp-room-containment": "Chirp — wall detected",
};

type MeshViewProps = UseElectionSocketResult;

export default function MeshView({
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
}: MeshViewProps) {
  const canWake = connection === "live";
  const outcomeById = new Map(wakeOutcome?.results.map((r) => [r.id, r.outcome]) ?? []);
  const isMigrating = handoffPhase !== "IDLE";
  // Tier-2 escalation is "live" when a contest is active. The chirp ping
  // animation arms when a fresh rangingEvent arrives and disarms after the
  // hook's timer clears it.
  const isContested = contest !== null;
  const rangingActive = rangingEvent !== null;

  return (
    <div className="space-y-6">
      {/* Hidden audio element driven by useElectionSocket. The hook loads the
          aggregator's TTS mp3 into it, pauses on TRANSFER, and resumes from
          the recorded offset on RELEASE — the "finishes its sentence on the
          next device" effect. */}
      <AudioBridge registerAudioElement={registerAudioElement} />

      {/* OWNER SPOTLIGHT */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Mesh Election
          </h2>
          <div className="flex items-center gap-3">
            {tick !== null && (
              <span className="font-mono text-xs text-slate-500">tick {tick}</span>
            )}
            <span
              className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest ${CONNECTION_PILL_CLASS[connection]}`}
            >
              {CONNECTION_LABEL[connection]}
            </span>
          </div>
        </div>

        {connection === "offline" && (
          <p className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-4 text-sm text-rose-300">
            Not connected to the mesh aggregator at {MESH_WS_URL}. Start the aggregator and this
            panel will connect automatically (retrying every {MESH_RETRY_MS / 1000}s).
          </p>
        )}

        {connection === "connecting" && (
          <p className="rounded-lg border border-slate-700 bg-slate-900 p-4 text-sm text-slate-400">
            Connecting to {MESH_WS_URL}…
          </p>
        )}

        {connection === "live" && (
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            {owner === null ? (
              <div className="flex flex-1 items-center gap-3 rounded-lg border border-slate-700 bg-slate-900 px-4 py-5">
                <Radio className="h-6 w-6 text-slate-500" />
                <span className="text-lg font-semibold uppercase tracking-widest text-slate-400">
                  No Owner
                </span>
              </div>
            ) : (
              <motion.div
                key={owner}
                initial={{ opacity: 0, scale: 0.97 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ type: "spring", duration: 0.4 }}
                className="relative flex flex-1 items-center gap-3 overflow-hidden rounded-lg border border-cyan-500/60 bg-cyan-500/10 px-4 py-5"
              >
                <motion.div
                  animate={{ opacity: [0.5, 0.15, 0.5] }}
                  transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
                  className="absolute inset-0 rounded-lg border-2 border-cyan-400"
                />
                <Radio className="h-6 w-6 text-cyan-400" />
                <span className="text-lg font-semibold uppercase tracking-widest text-cyan-300">
                  {owner}
                </span>
                <span className="ml-auto rounded bg-cyan-500/20 px-2 py-1 text-[10px] font-semibold uppercase text-cyan-300">
                  owner
                </span>
              </motion.div>
            )}

            <button
              onClick={sendWake}
              disabled={!canWake}
              className="flex items-center justify-center gap-2 rounded-lg bg-cyan-500 px-5 py-4 text-sm font-semibold text-slate-950 transition-colors hover:bg-cyan-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              title="Broadcast a wake request; only the current owner should answer."
            >
              <Zap className="h-4 w-4" />
              Wake
            </button>
          </div>
        )}
      </section>

      {/* CONVERSATION (Phase 3) */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Conversation
          </h2>
          <span className="text-xs text-slate-500">
            {speakingScanner !== null ? `speaking: ${speakingScanner}` : "idle"}
          </span>
        </div>

        <AnimatePresence>
          {isMigrating && phaseFrom !== null && phaseTo !== null && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="mb-4 overflow-hidden rounded-lg border border-amber-500/40 bg-amber-500/5 p-3"
            >
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-semibold uppercase tracking-widest text-amber-300">
                  Migrating utterance
                </span>
                <span className="font-mono text-slate-400">
                  {phaseFrom} <ArrowRight className="inline h-3 w-3" /> {phaseTo}
                </span>
              </div>
              <HandoffPhasePill currentPhase={handoffPhase} />
            </motion.div>
          )}
        </AnimatePresence>

        <SayInput
          disabled={connection !== "live" || isMigrating || owner === null}
          onSend={sendSay}
        />

        {utterance !== null && utterance.isSynthetic && (
          <p className="mt-2 text-[10px] uppercase tracking-widest text-amber-400/80">
            Voice service offline — showing word progress without audio
          </p>
        )}

        <div className="mt-4 max-h-40 space-y-1.5 overflow-y-auto pr-1">
          {transcript.length === 0 && (
            <p className="py-4 text-center text-sm text-slate-600">
              No conversation yet — type a message above and the current owner will speak it.
            </p>
          )}
          <AnimatePresence initial={false}>
            {transcript.map((entry) => (
              <motion.div
                key={entry.id}
                initial={{ x: -40, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ opacity: 0 }}
                className="rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm"
              >
                <div className="mb-0.5 flex items-center justify-between text-[10px] text-slate-500">
                  <span className="font-mono">
                    <span
                      className={
                        speakingScanner === entry.scanner
                          ? "font-semibold text-cyan-300"
                          : "text-slate-400"
                      }
                    >
                      {entry.scanner}
                    </span>
                    <span className="ml-2 uppercase tracking-widest text-slate-600">
                      {entry.role}
                    </span>
                  </span>
                  <span className="font-mono">{entry.ts}</span>
                </div>
                <div className="text-slate-200">{entry.text}</div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </section>

      {/* TIER-2 RANGING (Phase 4) — only rendered when a contest is live or a
          chirp resolution is on hand. The panel visualizes the escalation:
          when two signal bars are within the margin, a near-ultrasound chirp
          fires (the ping animation) and resolves the tie deterministically. */}
      <AnimatePresence>
        {(isContested || chirp !== null) && (
          <motion.section
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden rounded-xl border border-fuchsia-500/40 bg-fuchsia-500/5 p-5"
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-fuchsia-300">
                <Waves className="h-3.5 w-3.5" />
                Tier 2 · Near-Ultrasound
              </h2>
              <span className="text-[10px] uppercase tracking-widest text-fuchsia-300/70">
                {isContested ? "Contested — escalating" : "Resolved"}
              </span>
            </div>

            {contest !== null && (
              <div className="mb-3 flex items-center justify-center gap-4 text-sm">
                <span className="font-mono text-cyan-300">{contest.incumbentId}</span>
                <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
                  {contest.incumbentRssi.toFixed(1)} / {contest.challengerRssi.toFixed(1)} dBm
                </span>
                <span className="font-mono text-cyan-300">{contest.challengerId}</span>
              </div>
            )}

            {/* Chirp ping animation: arms when rangingEvent arrives, shows
                expanding rings between the contest parties while the chirp
                "travels". Disarms when the hook's timer clears rangingEvent. */}
            <ChirpPing armed={rangingActive} />

            {chirp !== null && chirp.winnerId !== null && (
              <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-xs">
                <span className="text-slate-400">Chirp winner:</span>
                <span className="font-mono font-semibold text-fuchsia-300">{chirp.winnerId}</span>
                <span className="text-slate-500">
                  · {Math.min(...chirp.measurements.map((m) => m.distanceM)).toFixed(2)} m ToF
                </span>
                {chirp.sameRoom ? (
                  <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-emerald-300">
                    Same room
                  </span>
                ) : (
                  <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-300">
                    Wall detected
                  </span>
                )}
              </div>
            )}
          </motion.section>
        )}
      </AnimatePresence>

      {/* PER-SCANNER RANKED LIST */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Scanners
          </h2>
          <span className="text-[10px] uppercase tracking-widest text-slate-500">
            {FUSION_REASON_LABEL[fusionReason]}
          </span>
        </div>
        {scanners.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-600">
            No scanner data yet — waiting for the aggregator.
          </p>
        ) : (
          <div className="space-y-2">
            {scanners.map((scanner) => {
              const isOwner = owner === scanner.id;
              const isSpeaking = speakingScanner === scanner.id && utterance !== null;
              const bars = scanner.smoothedRssi !== null ? getBars(scanner.smoothedRssi) : 0;
              const badge = outcomeById.get(scanner.id);
              return (
                <div
                  key={scanner.id}
                  className={`rounded-lg border p-3 transition-colors ${
                    !scanner.present
                      ? "border-slate-800 bg-slate-900/40 opacity-50"
                      : isOwner
                        ? "border-cyan-500/60 bg-cyan-500/10"
                        : "border-slate-800 bg-slate-900"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-sm font-medium ${
                          isOwner ? "text-cyan-300" : "text-slate-300"
                        }`}
                      >
                        {scanner.id}
                      </span>
                      {isOwner && (
                        <span className="rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-cyan-300">
                          active
                        </span>
                      )}
                      {isSpeaking && <SpeakingWave />}
                      {!scanner.present && (
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-500">
                          absent
                        </span>
                      )}
                      <AnimatePresence>
                        {badge !== undefined && (
                          <motion.span
                            initial={{ opacity: 0, y: -4 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0 }}
                            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                              badge === "ACCEPTED"
                                ? "bg-cyan-500/30 text-cyan-200"
                                : "bg-slate-800 text-slate-500"
                            }`}
                          >
                            {badge}
                          </motion.span>
                        )}
                      </AnimatePresence>
                    </div>
                    <span className="font-mono text-xs text-slate-400">
                      {scanner.smoothedRssi !== null ? `${scanner.smoothedRssi.toFixed(1)} dBm` : "—"}
                    </span>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
                    <motion.div
                      animate={{ width: `${(bars / 5) * 100}%` }}
                      transition={{ duration: 0.4, ease: "easeOut" }}
                      className={`h-full rounded-full ${isOwner ? "bg-cyan-400" : "bg-slate-600"}`}
                    />
                  </div>
                  <div className="mt-1.5 flex justify-between text-[10px] text-slate-500">
                    <span>
                      {scanner.rssi !== null ? `raw ${scanner.rssi.toFixed(1)} dBm` : "raw —"}
                    </span>
                    <span>
                      {scanner.present ? `${bars}/5 bars` : "no signal"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* CROSS-NODE HANDOFF LOG */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Cross-Node Handoff Log
          </h2>
          <span className="text-xs text-slate-500">{handoffLog.length} handoffs</span>
        </div>
        <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
          {handoffLog.length === 0 && (
            <p className="py-4 text-center text-sm text-slate-600">
              No handoffs yet — waiting for the mesh to hand off ownership between scanners.
            </p>
          )}
          <AnimatePresence initial={false}>
            {handoffLog.map((event) => (
              <motion.div
                key={event.id}
                initial={{ x: -40, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm"
              >
                <span className="font-mono text-xs text-slate-500">{event.time}</span>
                <span className="font-medium text-slate-300">{event.from}</span>
                <ArrowRight className="h-3.5 w-3.5 text-cyan-400" />
                <span className="font-medium text-cyan-300">{event.to}</span>
                <span className="ml-auto font-mono text-xs text-slate-500">
                  tick {event.atTick}
                </span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** The hidden <audio> element, registered with the hook on mount. Rendered
 * once at the top of MeshView; the hook owns playback from there. */
function AudioBridge({
  registerAudioElement,
}: {
  registerAudioElement: (el: HTMLAudioElement | null) => void;
}) {
  const ref = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    registerAudioElement(ref.current);
    return () => registerAudioElement(null);
  }, [registerAudioElement]);

  return <audio ref={ref} hidden />;
}

/** The 4-step PREPARE -> TRANSFER -> CONFIRM -> RELEASE progress indicator. */
function HandoffPhasePill({ currentPhase }: { currentPhase: HandoffPhase }) {
  const currentIdx = HANDOFF_PHASE_ORDER.indexOf(currentPhase);
  return (
    <div className="flex items-center gap-1.5">
      {HANDOFF_PHASE_ORDER.map((phase, idx) => {
        const isCurrent = idx === currentIdx;
        const isDone = idx < currentIdx;
        return (
          <div key={phase} className="flex flex-1 items-center gap-1.5">
            <motion.div
              animate={{
                backgroundColor: isCurrent
                  ? "rgba(34, 211, 238, 0.9)"
                  : isDone
                    ? "rgba(34, 211, 238, 0.3)"
                    : "rgba(51, 65, 85, 0.6)",
                scale: isCurrent ? 1.1 : 1,
              }}
              transition={{ duration: 0.2 }}
              className="h-2 flex-1 rounded-full"
            />
            <span
              className={`text-[9px] font-semibold uppercase tracking-widest ${
                isCurrent ? "text-cyan-300" : isDone ? "text-cyan-500/60" : "text-slate-600"
              }`}
            >
              {phase}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** Animated equalizer-style bars shown on the speaking scanner's card. */
function SpeakingWave() {
  return (
    <span className="flex items-end gap-0.5" aria-label="speaking">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="w-0.5 rounded-full bg-cyan-400"
          animate={{ height: ["4px", "10px", "4px"] }}
          transition={{
            duration: 0.5,
            repeat: Infinity,
            ease: "easeInOut",
            delay: i * 0.12,
          }}
          style={{ height: "4px" }}
        />
      ))}
    </span>
  );
}

/** The Phase 4 chirp ping: concentric expanding rings centered between the
 * contest parties. Arms when a rangingEvent arrives (a fresh chirp fired),
 * idles otherwise. The animation conveys the on-demand tier-2 escalation —
 * the visual answer to "why did a chirp just fire?" */
function ChirpPing({ armed }: { armed: boolean }) {
  return (
    <div className="relative flex h-12 items-center justify-center" aria-label="chirp">
      <AnimatePresence>
        {armed && (
          <>
            {[0, 1, 2].map((i) => (
              <motion.span
                key={i}
                className="absolute h-8 w-8 rounded-full border border-fuchsia-400"
                initial={{ scale: 0.4, opacity: 0.9 }}
                animate={{ scale: 2.4, opacity: 0 }}
                exit={{ opacity: 0 }}
                transition={{
                  duration: 1.4,
                  repeat: Infinity,
                  ease: "easeOut",
                  delay: i * 0.35,
                }}
              />
            ))}
            <motion.span
              className="rounded-full bg-fuchsia-400"
              animate={{ scale: [1, 1.3, 1] }}
              transition={{ duration: 0.7, repeat: Infinity, ease: "easeInOut" }}
              style={{ height: 8, width: 8 }}
            />
          </>
        )}
      </AnimatePresence>
      {!armed && (
        <span className="text-[10px] uppercase tracking-widest text-slate-600">
          Listening…
        </span>
      )}
    </div>
  );
}

/** The "Say something" input + Send button. Manages its own text state. */
function SayInput({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <div className="flex gap-2">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !disabled) submit();
        }}
        disabled={disabled}
        placeholder={
          disabled ? "Connect to the mesh to speak…" : "Type a message for the owner to speak…"
        }
        className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-sm text-slate-100 placeholder:text-slate-500 focus:border-cyan-500/60 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
      />
      <button
        onClick={submit}
        disabled={disabled || !text.trim()}
        className="flex items-center justify-center gap-2 rounded-lg bg-cyan-500 px-4 py-3 text-sm font-semibold text-slate-950 transition-colors hover:bg-cyan-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        title="Send the text to the aggregator; the current owner will speak it."
      >
        <Send className="h-4 w-4" />
        Say
      </button>
    </div>
  );
}
