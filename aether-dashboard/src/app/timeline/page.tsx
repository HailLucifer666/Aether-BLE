"use client";

import { useEffect, useRef, useState } from "react";
import NavBar from "../components/NavBar";
import { useElectionSocket } from "../mesh/useElectionSocket";

const MAX_BUFFER_ENTRIES = 500;

type TimelineEventKind = "election" | "handoff" | "ranging" | "position";

interface TimelineEntry {
  id: number;
  kind: TimelineEventKind;
  ts: number;
  summary: string;
  detail: Record<string, unknown>;
}

const KIND_COLOR: Record<TimelineEventKind, string> = {
  election: "bg-cyan-400",
  handoff: "bg-cyan-400",
  ranging: "bg-red-400",
  position: "bg-amber-500",
};

/**
 * Timeline — client-side rolling buffer (capped at MAX_BUFFER_ENTRIES) of
 * every election/handoff/ranging/position update this session has observed,
 * rendered as a scrubbable strip. No backend replay: this buffer only ever
 * grows from live messages the hook has already surfaced, and is discarded
 * on refresh.
 */
export default function TimelinePage() {
  const socket = useElectionSocket(true);
  const [entries, setEntries] = useState<TimelineEntry[]>([]);
  const idRef = useRef(0);
  const [scrubIndex, setScrubIndex] = useState(0);

  const lastLoggedTickRef = useRef<number | null>(null);
  const lastLoggedHandoffTickRef = useRef<number | null>(null);
  const lastLoggedRangingEventTickRef = useRef<number | null>(null);
  const lastLoggedPositionRef = useRef<Map<string, string>>(new Map());

  const pushEntry = (entry: Omit<TimelineEntry, "id">) => {
    setEntries((prev) => {
      const next = [...prev, { ...entry, id: idRef.current++ }].slice(-MAX_BUFFER_ENTRIES);
      return next;
    });
  };

  // Election ticks.
  useEffect(() => {
    if (socket.tick === null || socket.tick === lastLoggedTickRef.current) return;
    lastLoggedTickRef.current = socket.tick;
    pushEntry({
      kind: "election",
      ts: Date.now(),
      summary: `tick ${socket.tick} — owner: ${socket.owner ?? "none"}`,
      detail: { tick: socket.tick, owner: socket.owner, scanners: socket.scanners },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket.tick]);

  // Handoffs (from the hook's own de-duplicated handoffLog head).
  useEffect(() => {
    const latest = socket.handoffLog[0];
    if (latest === undefined || latest.atTick === lastLoggedHandoffTickRef.current) return;
    lastLoggedHandoffTickRef.current = latest.atTick;
    pushEntry({
      kind: "handoff",
      ts: Date.now(),
      summary: `handoff ${latest.from} -> ${latest.to} @ tick ${latest.atTick}`,
      detail: { ...latest },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket.handoffLog]);

  // Ranging events (one-shot chirp rounds).
  useEffect(() => {
    if (socket.rangingEvent === null) return;
    if (socket.rangingEvent.atTick === lastLoggedRangingEventTickRef.current) return;
    lastLoggedRangingEventTickRef.current = socket.rangingEvent.atTick;
    pushEntry({
      kind: "ranging",
      ts: Date.now(),
      summary: `chirp @ tick ${socket.rangingEvent.atTick} — winner: ${socket.rangingEvent.winnerId ?? "none"}`,
      detail: { ...socket.rangingEvent, fusionReason: socket.fusionReason },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket.rangingEvent]);

  // Position updates, keyed per userId so we only log a genuinely new fix.
  useEffect(() => {
    for (const [userId, pos] of socket.positions.entries()) {
      const key = `${pos.x.toFixed(2)},${pos.y.toFixed(2)},${pos.uncertaintyRadiusM.toFixed(2)}`;
      if (lastLoggedPositionRef.current.get(userId) === key) continue;
      lastLoggedPositionRef.current.set(userId, key);
      pushEntry({
        kind: "position",
        ts: Date.now(),
        summary: `${userId} @ (${pos.x.toFixed(2)}, ${pos.y.toFixed(2)}) ±${pos.uncertaintyRadiusM.toFixed(2)}m`,
        detail: { userId, ...pos },
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket.positions]);

  useEffect(() => {
    setScrubIndex(entries.length > 0 ? entries.length - 1 : 0);
  }, [entries.length]);

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(entries, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `aether-timeline-${Date.now()}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const visible = entries.slice(0, scrubIndex + 1);
  const selected = entries[scrubIndex];

  return (
    <div className="min-h-screen bg-[#0a0f1e] text-slate-100">
      <NavBar />
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Timeline</h1>
            <p className="text-xs text-slate-500">
              Rolling buffer of election/handoff/ranging/position events (capped at{" "}
              {MAX_BUFFER_ENTRIES}). Client-side only — no backend replay.
            </p>
          </div>
          <button
            onClick={handleExport}
            disabled={entries.length === 0}
            className="rounded-lg border border-cyan-400/60 bg-cyan-400/10 px-4 py-2 text-xs font-semibold uppercase tracking-widest text-cyan-300 transition-colors hover:bg-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Export JSON
          </button>
        </header>

        {entries.length === 0 ? (
          <p className="rounded-xl border border-white/5 bg-white/[0.02] p-6 text-center text-sm text-slate-600">
            No events observed yet — waiting for the aggregator.
          </p>
        ) : (
          <>
            <section className="rounded-xl border border-white/5 bg-white/[0.02] p-4 backdrop-blur">
              <div className="mb-3 flex h-10 items-end gap-px overflow-hidden rounded-lg bg-[#060911] px-1">
                {entries.map((entry, idx) => (
                  <span
                    key={entry.id}
                    className={`inline-block w-1 flex-shrink-0 rounded-sm ${KIND_COLOR[entry.kind]} ${
                      idx <= scrubIndex ? "opacity-90" : "opacity-20"
                    }`}
                    style={{ height: idx === scrubIndex ? "100%" : "60%" }}
                    title={entry.summary}
                  />
                ))}
              </div>
              <input
                type="range"
                min={0}
                max={entries.length - 1}
                value={scrubIndex}
                onChange={(e) => setScrubIndex(Number.parseInt(e.target.value, 10))}
                className="w-full accent-cyan-400"
              />
              <div className="mt-2 flex justify-between text-[10px] text-slate-500">
                <span>event {scrubIndex + 1} / {entries.length}</span>
                <span>{visible.length} shown up to scrub point</span>
              </div>
            </section>

            {selected !== undefined && (
              <section className="rounded-xl border border-white/5 bg-white/[0.02] p-4 backdrop-blur">
                <div className="mb-2 flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${KIND_COLOR[selected.kind]}`} />
                  <span className="text-xs font-semibold uppercase tracking-widest text-slate-400">
                    {selected.kind}
                  </span>
                  <span className="text-xs text-slate-500">{selected.summary}</span>
                </div>
                <pre className="max-h-64 overflow-auto rounded-lg bg-[#060911] p-3 text-[11px] text-slate-400">
                  {JSON.stringify(selected.detail, null, 2)}
                </pre>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
