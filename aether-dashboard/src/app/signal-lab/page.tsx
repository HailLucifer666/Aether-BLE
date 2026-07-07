"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import NavBar from "../components/NavBar";
import { useElectionSocket } from "../mesh/useElectionSocket";
import type { ScannerEntry } from "../mesh/types";

const HISTORY_WINDOW_MS = 60_000;
const CHART_WIDTH = 640;
const CHART_HEIGHT = 140;
const CHART_MIN_RSSI = -100;
const CHART_MAX_RSSI = -30;
const TUNING_DEBOUNCE_MS = 300;

// Client-side range clamps mirroring the server's validation bounds, so the
// UI itself never sends nonsense even though the server silently drops
// out-of-range values regardless.
const HYSTERESIS_DB_MIN = 0;
const HYSTERESIS_DB_MAX = 20;
const CONSECUTIVE_TICKS_MIN = 1;
const CONSECUTIVE_TICKS_MAX = 20;
const CONTEST_MARGIN_DB_MIN = 0;
const CONTEST_MARGIN_DB_MAX = 20;

const DEFAULT_HYSTERESIS_DB = 8.0;
const DEFAULT_CONSECUTIVE_TICKS = 3;
const DEFAULT_CONTEST_MARGIN_DB = 4.0;

interface RssiSample {
  ts: number;
  rssi: number | null;
  smoothedRssi: number | null;
}

/**
 * Signal Lab — rolling 60s RSSI history per scanner (raw vs. server-smoothed,
 * both fields already computed server-side on ScannerEntry — no client
 * smoothing math here), a hysteresis threshold band, and three tuning
 * sliders wired to setTuning (debounced ~300ms).
 */
export default function SignalLabPage() {
  const socket = useElectionSocket(true);
  const [historyByScanner, setHistoryByScanner] = useState<Map<string, RssiSample[]>>(new Map());

  const [hysteresisDb, setHysteresisDb] = useState(DEFAULT_HYSTERESIS_DB);
  const [consecutiveTicks, setConsecutiveTicks] = useState(DEFAULT_CONSECUTIVE_TICKS);
  const [contestMarginDb, setContestMarginDb] = useState(DEFAULT_CONTEST_MARGIN_DB);
  const tuningDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Append a sample per scanner on every election tick, trimming anything
  // older than the 60s window.
  useEffect(() => {
    if (socket.tick === null) return;
    const now = Date.now();
    setHistoryByScanner((prev) => {
      const next = new Map(prev);
      for (const scanner of socket.scanners) {
        const existing = next.get(scanner.id) ?? [];
        const appended = [
          ...existing,
          { ts: now, rssi: scanner.rssi, smoothedRssi: scanner.smoothedRssi },
        ].filter((s) => now - s.ts <= HISTORY_WINDOW_MS);
        next.set(scanner.id, appended);
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket.tick]);

  useEffect(() => {
    return () => {
      if (tuningDebounceRef.current !== null) clearTimeout(tuningDebounceRef.current);
    };
  }, []);

  const scheduleSendTuning = (next: {
    hysteresisDb: number;
    consecutiveTicks: number;
    contestMarginDb: number;
  }) => {
    if (tuningDebounceRef.current !== null) clearTimeout(tuningDebounceRef.current);
    tuningDebounceRef.current = setTimeout(() => {
      socket.sendSetTuning(next.hysteresisDb, next.consecutiveTicks, next.contestMarginDb);
    }, TUNING_DEBOUNCE_MS);
  };

  const onHysteresisChange = (value: number) => {
    const clamped = Math.max(HYSTERESIS_DB_MIN, Math.min(HYSTERESIS_DB_MAX, value));
    setHysteresisDb(clamped);
    scheduleSendTuning({ hysteresisDb: clamped, consecutiveTicks, contestMarginDb });
  };

  const onConsecutiveTicksChange = (value: number) => {
    const clamped = Math.round(
      Math.max(CONSECUTIVE_TICKS_MIN, Math.min(CONSECUTIVE_TICKS_MAX, value))
    );
    setConsecutiveTicks(clamped);
    scheduleSendTuning({ hysteresisDb, consecutiveTicks: clamped, contestMarginDb });
  };

  const onContestMarginChange = (value: number) => {
    const clamped = Math.max(CONTEST_MARGIN_DB_MIN, Math.min(CONTEST_MARGIN_DB_MAX, value));
    setContestMarginDb(clamped);
    scheduleSendTuning({ hysteresisDb, consecutiveTicks, contestMarginDb: clamped });
  };

  return (
    <div className="min-h-screen bg-[#0a0f1e] text-slate-100">
      <NavBar />
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        <header>
          <h1 className="text-lg font-semibold tracking-tight">Signal Lab</h1>
          <p className="text-xs text-slate-500">
            Rolling 60s RSSI history per scanner. Sliders push setTuning live to the running
            aggregator (debounced).
          </p>
        </header>

        <section className="rounded-xl border border-white/5 bg-white/[0.02] p-4 backdrop-blur">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
            Live Tuning
          </h2>
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
            <TuningSlider
              label="Hysteresis (dB)"
              value={hysteresisDb}
              min={HYSTERESIS_DB_MIN}
              max={HYSTERESIS_DB_MAX}
              step={0.5}
              onChange={onHysteresisChange}
            />
            <TuningSlider
              label="Consecutive ticks"
              value={consecutiveTicks}
              min={CONSECUTIVE_TICKS_MIN}
              max={CONSECUTIVE_TICKS_MAX}
              step={1}
              onChange={onConsecutiveTicksChange}
            />
            <TuningSlider
              label="Contest margin (dB)"
              value={contestMarginDb}
              min={CONTEST_MARGIN_DB_MIN}
              max={CONTEST_MARGIN_DB_MAX}
              step={0.5}
              onChange={onContestMarginChange}
            />
          </div>
        </section>

        {socket.scanners.length === 0 ? (
          <p className="rounded-xl border border-white/5 bg-white/[0.02] p-6 text-center text-sm text-slate-600">
            No scanners reporting yet — waiting for the aggregator.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {socket.scanners.map((scanner) => (
              <RssiChart
                key={scanner.id}
                scanner={scanner}
                history={historyByScanner.get(scanner.id) ?? []}
                hysteresisDb={hysteresisDb}
                isOwner={socket.owner === scanner.id}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

function TuningSlider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="flex flex-col gap-2 text-xs text-slate-400">
      <span className="flex items-center justify-between uppercase tracking-widest">
        {label}
        <span className="font-mono text-cyan-300">{value.toFixed(step < 1 ? 1 : 0)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number.parseFloat(e.target.value))}
        className="accent-cyan-400"
      />
    </label>
  );
}

function RssiChart({
  scanner,
  history,
  hysteresisDb,
  isOwner,
}: {
  scanner: ScannerEntry;
  history: RssiSample[];
  hysteresisDb: number;
  isOwner: boolean;
}) {
  const { rawPoints, smoothedPoints, bandY, bandHeight } = useMemo(() => {
    const span = CHART_MAX_RSSI - CHART_MIN_RSSI;
    const now = Date.now();
    const toXY = (sample: RssiSample, value: number): string => {
      const ageMs = now - sample.ts;
      const x = CHART_WIDTH - (ageMs / HISTORY_WINDOW_MS) * CHART_WIDTH;
      const clamped = Math.max(CHART_MIN_RSSI, Math.min(CHART_MAX_RSSI, value));
      const y = CHART_HEIGHT - ((clamped - CHART_MIN_RSSI) / span) * CHART_HEIGHT;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    };
    const raw = history
      .filter((s) => s.rssi !== null)
      .map((s) => toXY(s, s.rssi as number))
      .join(" ");
    const smoothed = history
      .filter((s) => s.smoothedRssi !== null)
      .map((s) => toXY(s, s.smoothedRssi as number))
      .join(" ");

    // Hysteresis band: shaded region spanning [smoothedRssi - hysteresisDb,
    // smoothedRssi] around the current smoothed value, i.e. the zone a
    // challenger must clear to trigger a handoff.
    const currentSmoothed = scanner.smoothedRssi;
    let y0 = 0;
    let h = 0;
    if (currentSmoothed !== null) {
      const top = Math.max(CHART_MIN_RSSI, Math.min(CHART_MAX_RSSI, currentSmoothed));
      const bottom = Math.max(CHART_MIN_RSSI, Math.min(CHART_MAX_RSSI, currentSmoothed - hysteresisDb));
      const yTop = CHART_HEIGHT - ((top - CHART_MIN_RSSI) / span) * CHART_HEIGHT;
      const yBottom = CHART_HEIGHT - ((bottom - CHART_MIN_RSSI) / span) * CHART_HEIGHT;
      y0 = yTop;
      h = yBottom - yTop;
    }
    return { rawPoints: raw, smoothedPoints: smoothed, bandY: y0, bandHeight: h };
  }, [history, hysteresisDb, scanner.smoothedRssi]);

  return (
    <section
      className={`rounded-xl border p-4 backdrop-blur ${
        isOwner ? "border-cyan-400/40 bg-cyan-400/5" : "border-white/5 bg-white/[0.02]"
      }`}
    >
      <div className="mb-2 flex items-center justify-between">
        <span className={`font-mono text-sm ${isOwner ? "text-cyan-300" : "text-slate-200"}`}>
          {scanner.id}
        </span>
        <span className="font-mono text-xs text-slate-500">
          {scanner.smoothedRssi !== null ? `${scanner.smoothedRssi.toFixed(1)} dBm` : "—"}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        className="h-32 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`RSSI history for ${scanner.id}`}
      >
        {bandHeight > 0 && (
          <rect
            x={0}
            y={bandY}
            width={CHART_WIDTH}
            height={bandHeight}
            className="fill-cyan-400/10"
          />
        )}
        <polyline points={rawPoints} fill="none" stroke="currentColor" className="text-slate-600" strokeWidth={1} />
        <polyline
          points={smoothedPoints}
          fill="none"
          stroke="currentColor"
          className="text-cyan-400"
          strokeWidth={1.5}
        />
      </svg>
      <div className="mt-1 flex items-center gap-4 text-[10px] text-slate-500">
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-3 bg-slate-600" /> raw
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-3 bg-cyan-400" /> smoothed
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 bg-cyan-400/10" /> hysteresis band
        </span>
      </div>
    </section>
  );
}
