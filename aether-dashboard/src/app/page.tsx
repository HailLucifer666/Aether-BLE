"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowRight,
  Gauge,
  Monitor,
  Play,
  Radio,
  Smartphone,
  Speaker,
  User,
  Wifi,
  type LucideIcon,
} from "lucide-react";

interface Device {
  name: string;
  position: number; // meters from origin
  baseRssi: number; // signal at 0m distance
  icon: string; // lucide icon name
  color: string; // tailwind class for bar color
}

interface HandoffEvent {
  id: number;
  from: string;
  to: string;
  rssi: number;
  time: string; // HH:MM:SS
}

type DataSource = "simulation" | "live";

type LiveConnectionState = "connecting" | "live" | "offline" | "beacon-lost";

/** Fixed schema published by the Python BLE bridge (ws://127.0.0.1:8765). Do not rename fields. */
interface LiveReadingMessage {
  type: "reading";
  scanner: string;
  name: string;
  rssi: number;
  smoothedRssi: number;
  lastSeenMs: number;
  ts: string;
}

interface LiveLostMessage {
  type: "lost";
  scanner: string;
  name: string;
  ts: string;
}

type LiveMessage = LiveReadingMessage | LiveLostMessage;

interface SparklineSample {
  rssi: number;
  smoothedRssi: number;
  ts: number; // Date.now() at receipt
}

const LIVE_WS_URL = "ws://127.0.0.1:8765";
const LIVE_RETRY_MS = 2000;
const LIVE_PRESENCE_RSSI_THRESHOLD = -85; // below this, treat beacon as effectively absent
const SPARKLINE_MAX_SAMPLES = 120;
const DISTANCE_PATH_LOSS_EXPONENT = 2.5;
const DEFAULT_P0_RSSI = -55; // documented sane default RSSI @ 1m before calibration
const CALIBRATION_STORAGE_KEY = "aether-ble-p0-rssi";
// The distance formula is exponential, so even the bridge's own EMA-smoothed
// RSSI (alpha=0.3) still produces a visibly jittery meters figure (residual
// dBm noise gets amplified). A slower secondary EMA, applied only to the
// value feeding the distance calculation, damps that without touching the
// raw dBm readout or sparkline (which should keep showing real noise).
const DISTANCE_DISPLAY_EMA_ALPHA = 0.15;

const DEVICES: readonly Device[] = [
  { name: "PC", position: -2, baseRssi: -45, icon: "monitor", color: "bg-violet-500" },
  { name: "Phone", position: 0, baseRssi: -55, icon: "smartphone", color: "bg-emerald-500" },
  { name: "Speaker", position: 3, baseRssi: -50, icon: "speaker", color: "bg-rose-500" },
];

const SIM_DEVICE_NAMES: readonly string[] = DEVICES.map((d) => d.name);
const LIVE_BEACON_NAME = "LiveBeacon";
const LIVE_CANDIDATE_NAMES: readonly string[] = [LIVE_BEACON_NAME];

const ICONS: Record<string, LucideIcon> = {
  monitor: Monitor,
  smartphone: Smartphone,
  speaker: Speaker,
};

const SIM_PATH: readonly number[] = [0, 0.5, 1.0, 2.0, 2.5, 3.0, 2.0, 1.0, 0, -1.0, -2.0, -2.5, -2.0];
const SIM_STEP_MS = 1200;
const TICK_MS = 600;
const POS_MIN = -2.5;
const POS_MAX = 3.5;
const MAP_MIN = -3;
const MAP_MAX = 4;
const MAX_LOG_ENTRIES = 20;
const HYSTERESIS_THRESHOLD_DB = 5;
const HYSTERESIS_CONSECUTIVE = 2;

// Live-BLE connection-status debounce: mirrors the same consecutive-streak
// hysteresis idea already used for simulated device handoff above, applied
// instead to the liveConnection "live" <-> "beacon-lost" transition. The
// bridge broadcasts every ~0.4s (BROADCAST_INTERVAL_SECONDS in bridge.py),
// and Windows' BLE scanning stack is known to produce normal multi-second
// reception gaps even with a healthy beacon, so a single stray low-RSSI
// sample or one delayed "reading" message must not be enough to flip the
// pill. Require a few consecutive lost-looking signals before declaring
// beacon-lost, and a few consecutive good signals before declaring live
// again, so a genuine sustained loss is still reported promptly.
const LIVE_LOST_CONSECUTIVE = 3;
const LIVE_RECOVERED_CONSECUTIVE = 2;

function calculateRssi(device: Device, userPos: number): number {
  const distance = Math.abs(userPos - device.position);
  const noise = (Math.random() - 0.5) * 8; // ±4 dBm jitter
  return device.baseRssi - distance * 6 + noise;
}

function getBars(rssi: number): number {
  return Math.max(0, Math.min(5, Math.floor((rssi + 90) / 8)));
}

function toPercent(position: number): number {
  return ((position - MAP_MIN) / (MAP_MAX - MAP_MIN)) * 100;
}

function timestamp(): string {
  return new Date().toLocaleTimeString("en-GB", { hour12: false });
}

export default function Dashboard() {
  const [userPosition, setUserPosition] = useState<number>(0);
  const [activeDevice, setActiveDevice] = useState<string | null>(null);
  const [handoffs, setHandoffs] = useState<HandoffEvent[]>([]);
  const [isSimulating, setIsSimulating] = useState<boolean>(false);
  const [hysteresisOn, setHysteresisOn] = useState<boolean>(true);
  const [readings, setReadings] = useState<Record<string, number>>({});

  const [dataSource, setDataSource] = useState<DataSource>("simulation");
  const [liveConnection, setLiveConnection] = useState<LiveConnectionState>("connecting");
  const [liveSmoothedRssi, setLiveSmoothedRssi] = useState<number | null>(null);
  const [liveRawRssi, setLiveRawRssi] = useState<number | null>(null);
  const [liveLastTs, setLiveLastTs] = useState<string | null>(null);
  const [sparkline, setSparkline] = useState<SparklineSample[]>([]);
  const [calibratedP0, setCalibratedP0] = useState<number>(DEFAULT_P0_RSSI);
  const [isCalibrated, setIsCalibrated] = useState<boolean>(false);
  const [distanceDisplayRssi, setDistanceDisplayRssi] = useState<number | null>(null);
  // Ref mirror of distanceDisplayRssi so the EMA in handleLiveReading can read
  // the previous value synchronously without depending on state (same reason
  // activeRef mirrors activeDevice elsewhere in this file).
  const distanceDisplayRssiRef = useRef<number | null>(null);

  const activeRef = useRef<string | null>(null);
  const challengerRef = useRef<{ name: string | null; streak: number }>({ name: null, streak: 0 });
  const eventIdRef = useRef<number>(0);
  const simTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Streaks of consecutive lost-looking / good-looking live-BLE signals,
  // used to debounce the liveConnection "live" <-> "beacon-lost" pill (see
  // LIVE_LOST_CONSECUTIVE / LIVE_RECOVERED_CONSECUTIVE above). Reset to 0
  // whenever the opposite signal arrives, mirroring challengerRef's pattern.
  const liveLostStreakRef = useRef<number>(0);
  const liveRecoveredStreakRef = useRef<number>(0);
  // Mirrors the liveConnection state in a ref (same reason activeRef mirrors
  // activeDevice) so the debounce callback below can read the latest value
  // without depending on state, keeping the WebSocket effect's callback
  // identities stable across liveConnection changes.
  const liveConnectionRef = useRef<LiveConnectionState>("connecting");

  const wsRef = useRef<WebSocket | null>(null);
  const wsRetryTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const arbitrate = useCallback(
    (rssiByDevice: Record<string, number>, candidateNames: readonly string[] = SIM_DEVICE_NAMES, beaconPresent?: boolean) => {
      // Live-BLE-only release path: when the single real beacon is absent/lost,
      // explicitly clear ownership. Simulation mode never passes `beaconPresent`
      // (always undefined there), so this branch is unreachable from Simulation.
      if (beaconPresent === false) {
        if (activeRef.current !== null) {
          activeRef.current = null;
          setActiveDevice(null);
        }
        challengerRef.current = { name: null, streak: 0 };
        return;
      }

      const best = candidateNames.reduce((a, b) =>
        rssiByDevice[b] > rssiByDevice[a] ? b : a
      );
      const current = activeRef.current;

      if (current === null) {
        activeRef.current = best;
        setActiveDevice(best);
        return;
      }
      if (best === current) {
        challengerRef.current = { name: null, streak: 0 };
        return;
      }

      let shouldHandoff = false;
      if (!hysteresisOn) {
        shouldHandoff = true;
      } else {
        const streak = challengerRef.current.name === best ? challengerRef.current.streak + 1 : 1;
        challengerRef.current = { name: best, streak };
        shouldHandoff =
          streak >= HYSTERESIS_CONSECUTIVE &&
          rssiByDevice[best] > rssiByDevice[current] + HYSTERESIS_THRESHOLD_DB;
      }

      if (shouldHandoff) {
        challengerRef.current = { name: null, streak: 0 };
        activeRef.current = best;
        setActiveDevice(best);
        const event: HandoffEvent = {
          id: eventIdRef.current++,
          from: current,
          to: best,
          rssi: Math.round(rssiByDevice[best] * 10) / 10,
          time: timestamp(),
        };
        setHandoffs((prev) => [event, ...prev].slice(0, MAX_LOG_ENTRIES));
      }
    },
    [hysteresisOn]
  );

  useEffect(() => {
    if (dataSource !== "simulation") return;
    const tick = () => {
      const next: Record<string, number> = {};
      for (const device of DEVICES) {
        next[device.name] = calculateRssi(device, userPosition);
      }
      setReadings(next);
      arbitrate(next);
    };
    tick();
    const id = setInterval(tick, TICK_MS);
    return () => clearInterval(id);
  }, [userPosition, arbitrate, dataSource]);

  useEffect(() => {
    return () => {
      if (simTimerRef.current !== null) clearInterval(simTimerRef.current);
    };
  }, []);

  const runSimulation = useCallback(() => {
    if (isSimulating) return;
    setIsSimulating(true);
    setUserPosition(SIM_PATH[0]);
    let step = 0;
    simTimerRef.current = setInterval(() => {
      step += 1;
      if (step >= SIM_PATH.length) {
        if (simTimerRef.current !== null) clearInterval(simTimerRef.current);
        simTimerRef.current = null;
        setIsSimulating(false);
        return;
      }
      setUserPosition(SIM_PATH[step]);
    }, SIM_STEP_MS);
  }, [isSimulating]);

  const handleTrackClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (isSimulating) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const ratio = (e.clientX - rect.left) / rect.width;
      const position = MAP_MIN + ratio * (MAP_MAX - MAP_MIN);
      setUserPosition(Math.max(POS_MIN, Math.min(POS_MAX, Math.round(position * 10) / 10)));
    },
    [isSimulating]
  );

  // Load any previously calibrated P0 from localStorage on mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(CALIBRATION_STORAGE_KEY);
    if (stored === null) return;
    const parsed = Number.parseFloat(stored);
    if (Number.isFinite(parsed)) {
      setCalibratedP0(parsed);
      setIsCalibrated(true);
    }
  }, []);

  // Keep liveConnectionRef mirrored to liveConnection state (same reason
  // activeRef mirrors activeDevice) so resolveBeaconPresence can read the
  // latest value without taking a state dependency.
  useEffect(() => {
    liveConnectionRef.current = liveConnection;
  }, [liveConnection]);

  // Debounced beacon-presence transition shared by handleLiveReading and
  // handleLiveLost: a single lost-looking or good-looking signal only bumps
  // its streak counter. The visible liveConnection pill (and the
  // beaconPresent flag fed into arbitrate's ownership pipeline) only flips
  // once LIVE_LOST_CONSECUTIVE / LIVE_RECOVERED_CONSECUTIVE consecutive
  // same-direction signals have arrived, so one missed/late message can't
  // cause a visible flicker while a genuine sustained loss still surfaces
  // within a couple of broadcast intervals.
  const resolveBeaconPresence = useCallback((signalPresent: boolean): boolean => {
    const wasLost = liveConnectionRef.current === "beacon-lost";
    if (signalPresent) {
      liveLostStreakRef.current = 0;
      liveRecoveredStreakRef.current += 1;
      if (!wasLost) return true;
      return liveRecoveredStreakRef.current >= LIVE_RECOVERED_CONSECUTIVE;
    }
    liveRecoveredStreakRef.current = 0;
    liveLostStreakRef.current += 1;
    if (wasLost) return false;
    return !(liveLostStreakRef.current >= LIVE_LOST_CONSECUTIVE);
  }, []);

  const handleLiveReading = useCallback((msg: LiveReadingMessage) => {
    setLiveSmoothedRssi(msg.smoothedRssi);
    setLiveRawRssi(msg.rssi);
    setLiveLastTs(msg.ts);

    const prevDistanceRssi = distanceDisplayRssiRef.current;
    const nextDistanceRssi =
      prevDistanceRssi === null
        ? msg.smoothedRssi
        : DISTANCE_DISPLAY_EMA_ALPHA * msg.smoothedRssi + (1 - DISTANCE_DISPLAY_EMA_ALPHA) * prevDistanceRssi;
    distanceDisplayRssiRef.current = nextDistanceRssi;
    setDistanceDisplayRssi(nextDistanceRssi);
    setSparkline((prev) =>
      [...prev, { rssi: msg.rssi, smoothedRssi: msg.smoothedRssi, ts: Date.now() }].slice(
        -SPARKLINE_MAX_SAMPLES
      )
    );

    const signalPresent = msg.smoothedRssi >= LIVE_PRESENCE_RSSI_THRESHOLD;
    const beaconPresent = resolveBeaconPresence(signalPresent);
    setLiveConnection(beaconPresent ? "live" : "beacon-lost");
    arbitrate({ [LIVE_BEACON_NAME]: msg.smoothedRssi }, LIVE_CANDIDATE_NAMES, beaconPresent);
  }, [arbitrate, resolveBeaconPresence]);

  const handleLiveLost = useCallback((msg: LiveLostMessage) => {
    setLiveLastTs(msg.ts);
    const beaconPresent = resolveBeaconPresence(false);
    setLiveConnection(beaconPresent ? "live" : "beacon-lost");
    arbitrate({}, LIVE_CANDIDATE_NAMES, beaconPresent);
  }, [arbitrate, resolveBeaconPresence]);

  // Live BLE WebSocket connection: connect while in "live" mode, retry every
  // 2s on close/error, tear down cleanly when leaving live mode.
  useEffect(() => {
    if (dataSource !== "live") {
      if (wsRetryTimerRef.current !== null) {
        clearInterval(wsRetryTimerRef.current);
        wsRetryTimerRef.current = null;
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
      setLiveConnection((prev) => (prev === "live" || prev === "beacon-lost" ? prev : "connecting"));
      const socket = new WebSocket(LIVE_WS_URL);
      wsRef.current = socket;

      socket.onopen = () => {
        if (cancelled) return;
        setLiveConnection("live");
      };

      socket.onmessage = (event) => {
        if (cancelled) return;
        let parsed: LiveMessage;
        try {
          parsed = JSON.parse(event.data as string) as LiveMessage;
        } catch {
          return;
        }
        if (parsed.type === "reading") {
          handleLiveReading(parsed);
        } else if (parsed.type === "lost") {
          handleLiveLost(parsed);
        }
      };

      const handleDisconnect = () => {
        if (cancelled) return;
        setLiveConnection("offline");
        wsRef.current = null;
      };
      socket.onclose = handleDisconnect;
      socket.onerror = handleDisconnect;
    };

    connect();
    wsRetryTimerRef.current = setInterval(() => {
      if (wsRef.current === null || wsRef.current.readyState === WebSocket.CLOSED) {
        connect();
      }
    }, LIVE_RETRY_MS);

    return () => {
      cancelled = true;
      if (wsRetryTimerRef.current !== null) {
        clearInterval(wsRetryTimerRef.current);
        wsRetryTimerRef.current = null;
      }
      if (wsRef.current !== null) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [dataSource, handleLiveReading, handleLiveLost]);

  const toggleDataSource = useCallback(() => {
    if (isSimulating) return;
    setDataSource((prev) => {
      const next = prev === "simulation" ? "live" : "simulation";
      // Reset ownership state on every switch so neither mode ever starts
      // with a stale activeDevice left over from the other mode.
      activeRef.current = null;
      setActiveDevice(null);
      challengerRef.current = { name: null, streak: 0 };
      if (next === "live") {
        // Reset live-only state so the sparkline/status restart clean each time.
        setLiveConnection("connecting");
        setLiveSmoothedRssi(null);
        setLiveRawRssi(null);
        setLiveLastTs(null);
        setSparkline([]);
        distanceDisplayRssiRef.current = null;
        setDistanceDisplayRssi(null);
        liveLostStreakRef.current = 0;
        liveRecoveredStreakRef.current = 0;
      }
      return next;
    });
  }, [isSimulating]);

  const handleCalibrate = useCallback(() => {
    if (distanceDisplayRssi === null) return;
    setCalibratedP0(distanceDisplayRssi);
    setIsCalibrated(true);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(CALIBRATION_STORAGE_KEY, String(distanceDisplayRssi));
    }
  }, [distanceDisplayRssi]);

  // Uses the slower-damped distanceDisplayRssi (not the raw liveSmoothedRssi
  // shown in the dBm readout) so the exponential distance formula doesn't
  // amplify residual RSSI noise into a visibly jittery meters figure.
  const liveDistanceMeters =
    distanceDisplayRssi !== null
      ? Math.pow(10, (calibratedP0 - distanceDisplayRssi) / (10 * DISTANCE_PATH_LOSS_EXPONENT))
      : null;

  return (
    <main className="min-h-screen bg-slate-950 px-4 py-8 text-slate-100">
      <div className="mx-auto max-w-6xl space-y-6">
        {/* HEADER */}
        <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
              <Radio className="h-6 w-6 text-cyan-400" />
              Aether Protocol <span className="text-cyan-400">v0.1</span>
            </h1>
            <p className="mt-1 text-sm text-slate-400">Cross-Device AI Arbitration</p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={toggleDataSource}
              disabled={isSimulating}
              className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                dataSource === "live"
                  ? "border-amber-500/60 bg-amber-500/10 text-amber-300"
                  : "border-slate-700 bg-slate-900 text-slate-400"
              }`}
              title="Switch between the fake 3-device simulation and a live BLE beacon over WebSocket."
            >
              <Wifi className="h-4 w-4" />
              Source: {dataSource === "live" ? "Live BLE" : "Simulation"}
            </button>
            {dataSource === "simulation" && (
              <button
                onClick={() => setHysteresisOn((v) => !v)}
                className={`rounded-lg border px-4 py-2 text-sm font-medium transition-colors ${
                  hysteresisOn
                    ? "border-cyan-500/60 bg-cyan-500/10 text-cyan-300"
                    : "border-slate-700 bg-slate-900 text-slate-400"
                }`}
                title="Naive = instant strongest-wins (flappy). Hysteresis = challenger must beat active by 5 dBm for 2 readings."
              >
                Arbitration: {hysteresisOn ? "Hysteresis" : "Naive"}
              </button>
            )}
            {dataSource === "simulation" && (
              <button
                onClick={runSimulation}
                disabled={isSimulating}
                className="flex items-center gap-2 rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-cyan-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              >
                <Play className="h-4 w-4" />
                {isSimulating ? "Simulating…" : "Run Simulation"}
              </button>
            )}
          </div>
        </header>

        {dataSource === "simulation" && (
        <div className="flex flex-col gap-6 lg:flex-row">
          {/* ROOM MAP */}
          <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 lg:w-[60%]">
            <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-slate-500">
              Room Map
            </h2>
            <div className="relative h-48">
              {/* baseline */}
              <div className="absolute left-0 right-0 top-1/2 h-px bg-slate-800" />
              {DEVICES.map((device) => {
                const Icon = ICONS[device.icon];
                const isActive = activeDevice === device.name;
                return (
                  <div
                    key={device.name}
                    className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
                    style={{ left: `${toPercent(device.position)}%` }}
                  >
                    <motion.div
                      animate={{ scale: isActive ? 1.2 : 1.0 }}
                      transition={{ type: "spring", duration: 0.4 }}
                      className={`relative flex h-16 w-16 flex-col items-center justify-center gap-1 rounded-xl border bg-slate-900 ${
                        isActive ? "border-white" : "border-slate-700"
                      }`}
                    >
                      {isActive && (
                        <motion.div
                          animate={{ opacity: [0.9, 0.1, 0.9], scale: [1, 1.25, 1] }}
                          transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
                          className="absolute inset-0 rounded-xl border-2 border-white"
                        />
                      )}
                      <Icon className={`h-6 w-6 ${isActive ? "text-cyan-400" : "text-slate-400"}`} />
                      <span className="text-[10px] font-medium text-slate-300">{device.name}</span>
                    </motion.div>
                    <div className="mt-2 text-center text-[10px] text-slate-600">
                      {device.position}m
                    </div>
                  </div>
                );
              })}
              {/* user dot */}
              <motion.div
                animate={{ left: `${toPercent(userPosition)}%` }}
                transition={{ type: "spring", duration: 0.5, bounce: 0.35 }}
                className="absolute top-1 z-10 -translate-x-1/2"
              >
                <div className="flex flex-col items-center gap-1">
                  <span className="text-[10px] font-semibold text-amber-400">
                    {userPosition.toFixed(1)}m
                  </span>
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-amber-500 shadow-lg shadow-amber-500/40">
                    <User className="h-4 w-4 text-slate-950" />
                  </div>
                </div>
              </motion.div>
            </div>
            {/* click track */}
            <div
              onClick={handleTrackClick}
              className={`relative mt-2 h-8 rounded-lg border border-slate-800 bg-gradient-to-r from-slate-900 via-slate-800 to-slate-900 ${
                isSimulating ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:border-slate-600"
              }`}
              title={isSimulating ? "Disabled during simulation" : "Click to move the user"}
            >
              <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] uppercase tracking-widest text-slate-500">
                {isSimulating ? "auto-walking…" : "click to move user"}
              </span>
            </div>
          </section>

          {/* SIGNAL PANEL */}
          <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 lg:w-[40%]">
            <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-slate-500">
              Signal Panel
            </h2>
            <div className="space-y-3">
              {DEVICES.map((device) => {
                const Icon = ICONS[device.icon];
                const rssi = readings[device.name];
                const isActive = activeDevice === device.name;
                const bars = rssi !== undefined ? getBars(rssi) : 0;
                const distance = Math.abs(userPosition - device.position);
                return (
                  <div
                    key={device.name}
                    className={`rounded-lg border p-3 transition-colors ${
                      isActive
                        ? "border-cyan-500/60 bg-cyan-500/10"
                        : "border-slate-800 bg-slate-900"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Icon className={`h-4 w-4 ${isActive ? "text-cyan-400" : "text-slate-400"}`} />
                        <span className="text-sm font-medium">{device.name}</span>
                        {isActive && (
                          <span className="rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-cyan-300">
                            active
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-xs text-slate-400">
                        {rssi !== undefined ? `${rssi.toFixed(1)} dBm` : "—"}
                      </span>
                    </div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
                      <motion.div
                        animate={{ width: `${(bars / 5) * 100}%` }}
                        transition={{ duration: 0.4, ease: "easeOut" }}
                        className={`h-full rounded-full ${device.color}`}
                      />
                    </div>
                    <div className="mt-1.5 flex justify-between text-[10px] text-slate-500">
                      <span>{distance.toFixed(1)}m away</span>
                      <span>{bars}/5 bars</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
        )}

        {dataSource === "live" && (
          <LiveBleView
            liveConnection={liveConnection}
            liveSmoothedRssi={liveSmoothedRssi}
            liveRawRssi={liveRawRssi}
            liveLastTs={liveLastTs}
            sparkline={sparkline}
            activeDevice={activeDevice}
            calibratedP0={calibratedP0}
            isCalibrated={isCalibrated}
            liveDistanceMeters={liveDistanceMeters}
            onCalibrate={handleCalibrate}
          />
        )}

        {/* HANDOFF LOG */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
              Handoff Log
            </h2>
            <span className="text-xs text-slate-500">{handoffs.length} handoffs</span>
          </div>
          <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
            {handoffs.length === 0 && (
              <p className="py-4 text-center text-sm text-slate-600">
                No handoffs yet — run the simulation or click the track to move around the room.
              </p>
            )}
            <AnimatePresence initial={false}>
              {handoffs.map((event) => (
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
                    {event.rssi.toFixed(1)} dBm
                  </span>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        </section>
      </div>
    </main>
  );
}

const CONNECTION_LABEL: Record<LiveConnectionState, string> = {
  connecting: "CONNECTING",
  live: "LIVE",
  offline: "NOT CONNECTED",
  "beacon-lost": "SIGNAL LOST",
};

const CONNECTION_PILL_CLASS: Record<LiveConnectionState, string> = {
  connecting: "border-slate-600 bg-slate-800 text-slate-300",
  live: "border-emerald-500/60 bg-emerald-500/10 text-emerald-300",
  offline: "border-rose-500/60 bg-rose-500/10 text-rose-300",
  "beacon-lost": "border-amber-500/60 bg-amber-500/10 text-amber-300",
};

interface SparklinePathArgs {
  samples: readonly SparklineSample[];
  width: number;
  height: number;
  minRssi: number;
  maxRssi: number;
}

// Cosmetic-only: a short moving average over the already-buffered samples,
// used purely for the plotted "smoothed" trace so the chart reads calmly.
// This never feeds back into liveSmoothedRssi/arbitration/distance - those
// keep using the bridge's real, responsive EMA value for correctness.
const SPARKLINE_VISUAL_SMOOTHING_WINDOW = 6;
// Fixed y-axis band covering typical operating RSSI range (close-range to
// the presence-loss threshold with margin), used instead of dynamic
// min/max-from-samples scaling to keep the chart visually stable.
const SPARKLINE_MIN_RSSI = -95;
const SPARKLINE_MAX_RSSI = -35;

function movingAverage(values: readonly number[], windowSize: number): number[] {
  return values.map((_, i) => {
    const start = Math.max(0, i - windowSize + 1);
    const windowSlice = values.slice(start, i + 1);
    return windowSlice.reduce((sum, v) => sum + v, 0) / windowSlice.length;
  });
}

function buildSparklinePoints({ samples, width, height, minRssi, maxRssi }: SparklinePathArgs): {
  raw: string;
  smoothed: string;
} {
  const span = Math.max(1, maxRssi - minRssi);
  const count = samples.length;
  const toXY = (value: number, index: number): string => {
    const x = count <= 1 ? width : (index / (count - 1)) * width;
    const clamped = Math.max(minRssi, Math.min(maxRssi, value));
    const y = height - ((clamped - minRssi) / span) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };
  const visuallySmoothed = movingAverage(
    samples.map((s) => s.smoothedRssi),
    SPARKLINE_VISUAL_SMOOTHING_WINDOW
  );
  return {
    raw: samples.map((s, i) => toXY(s.rssi, i)).join(" "),
    smoothed: visuallySmoothed.map((v, i) => toXY(v, i)).join(" "),
  };
}

interface LiveBleViewProps {
  liveConnection: LiveConnectionState;
  liveSmoothedRssi: number | null;
  liveRawRssi: number | null;
  liveLastTs: string | null;
  sparkline: readonly SparklineSample[];
  activeDevice: string | null;
  calibratedP0: number;
  isCalibrated: boolean;
  liveDistanceMeters: number | null;
  onCalibrate: () => void;
}

function LiveBleView({
  liveConnection,
  liveSmoothedRssi,
  liveRawRssi,
  liveLastTs,
  sparkline,
  activeDevice,
  calibratedP0,
  isCalibrated,
  liveDistanceMeters,
  onCalibrate,
}: LiveBleViewProps) {
  const bars = liveSmoothedRssi !== null ? getBars(liveSmoothedRssi) : 0;
  const hasOwner = activeDevice !== null;

  // Fixed y-axis band (not derived from the current sample window) so the
  // chart doesn't rescale/snap on every render as extreme samples enter or
  // leave the buffer - a few real dB of wobble should look like a few real
  // dB of wobble, not a dramatic full-height swing.
  const minRssi = SPARKLINE_MIN_RSSI;
  const maxRssi = SPARKLINE_MAX_RSSI;
  const sparklineWidth = 560;
  const sparklineHeight = 96;
  const { raw: rawPoints, smoothed: smoothedPoints } = buildSparklinePoints({
    samples: sparkline,
    width: sparklineWidth,
    height: sparklineHeight,
    minRssi,
    maxRssi,
  });

  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      {/* LIVE STATUS + DISTANCE */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 lg:w-[60%]">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Live BLE
          </h2>
          <span
            className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest ${CONNECTION_PILL_CLASS[liveConnection]}`}
          >
            {CONNECTION_LABEL[liveConnection]}
          </span>
        </div>

        {liveConnection === "offline" && (
          <p className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-4 text-sm text-rose-300">
            Not connected to the BLE bridge at {LIVE_WS_URL}. Start the Python bridge and this
            panel will connect automatically (retrying every {LIVE_RETRY_MS / 1000}s).
          </p>
        )}

        {liveConnection === "connecting" && (
          <p className="rounded-lg border border-slate-700 bg-slate-900 p-4 text-sm text-slate-400">
            Connecting to {LIVE_WS_URL}…
          </p>
        )}

        {liveConnection === "beacon-lost" && (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-300">
            SIGNAL LOST — the beacon stopped advertising. This can happen if the phone&apos;s
            screen locked, Bluetooth was toggled off, or the user walked away; the bridge cannot
            distinguish which.
          </p>
        )}

        {liveConnection === "live" && (
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="flex h-16 w-16 flex-col items-center justify-center gap-1 rounded-xl border border-cyan-500/60 bg-cyan-500/10">
                <Smartphone className="h-6 w-6 text-cyan-400" />
                <span className="text-[10px] font-medium text-slate-300">Phone</span>
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-slate-200">
                    {hasOwner ? "Active owner" : "No owner"}
                  </span>
                  <span className="font-mono text-xs text-slate-400">
                    {liveSmoothedRssi !== null ? `${liveSmoothedRssi.toFixed(1)} dBm` : "—"}
                  </span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
                  <motion.div
                    animate={{ width: `${(bars / 5) * 100}%` }}
                    transition={{ duration: 0.4, ease: "easeOut" }}
                    className="h-full rounded-full bg-emerald-500"
                  />
                </div>
                <div className="mt-1.5 flex justify-between text-[10px] text-slate-500">
                  <span>{liveLastTs ?? "—"}</span>
                  <span>{bars}/5 bars</span>
                </div>
              </div>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-900 p-3">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-sm font-medium text-slate-200">
                  <Gauge className="h-4 w-4 text-amber-400" />
                  Estimated distance
                </span>
                <button
                  onClick={onCalibrate}
                  className="rounded-lg border border-amber-500/60 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-300 transition-colors hover:bg-amber-500/20"
                  title="Store the current smoothed RSSI as the 1-meter reference (P0)."
                >
                  Calibrate @ 1m
                </button>
              </div>
              <p className="mt-2 font-mono text-2xl text-slate-100">
                {liveDistanceMeters !== null ? `~${liveDistanceMeters.toFixed(1)} m` : "—"}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                Estimate only, based on a log-distance path-loss model (n = {DISTANCE_PATH_LOSS_EXPONENT}).
                {isCalibrated
                  ? ` Calibrated P0 = ${calibratedP0.toFixed(1)} dBm.`
                  : ` Not yet calibrated — using default P0 = ${DEFAULT_P0_RSSI} dBm.`}
              </p>
            </div>
          </div>
        )}
      </section>

      {/* SPARKLINE */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 lg:w-[40%]">
        <h2 className="mb-4 text-xs font-semibold uppercase tracking-widest text-slate-500">
          RSSI Sparkline
        </h2>
        {sparkline.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-600">
            No samples yet — waiting for live readings.
          </p>
        ) : (
          <svg
            viewBox={`0 0 ${sparklineWidth} ${sparklineHeight}`}
            className="h-24 w-full"
            preserveAspectRatio="none"
            role="img"
            aria-label="RSSI over time, raw and smoothed"
          >
            <polyline
              points={rawPoints}
              fill="none"
              stroke="currentColor"
              className="text-slate-600"
              strokeWidth={1}
            />
            <polyline
              points={smoothedPoints}
              fill="none"
              stroke="currentColor"
              className="text-cyan-400"
              strokeWidth={1.5}
            />
          </svg>
        )}
        <div className="mt-2 flex items-center gap-4 text-[10px] text-slate-500">
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-3 bg-slate-600" /> raw
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-3 bg-cyan-400" /> smoothed
          </span>
          <span className="ml-auto">
            {liveRawRssi !== null ? `raw ${liveRawRssi.toFixed(1)} dBm` : ""}
          </span>
        </div>
      </section>
    </div>
  );
}
