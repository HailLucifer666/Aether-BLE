"use client";

import { useCallback, useState } from "react";
import NavBar from "../components/NavBar";
import FloorPlan, { type ScannerPlacement } from "../components/FloorPlan";
import { useElectionSocket } from "../mesh/useElectionSocket";

const RSSI_AT_1M_DEFAULT = -59;
const PATH_LOSS_EXPONENT_DEFAULT = 2.5;

interface CalibrationDraft {
  rssiAt1m: string;
  pathLossExponent: string;
}

/**
 * Setup wizard — lists every scanner id known from the latest election
 * message's scanners[] (the only discovery mechanism), lets the user
 * drag-place it on the shared FloorPlan, and submits per-scanner calibration
 * on blur/submit (never on every keystroke). No local arbitration: the
 * calibration values are just numbers the server will apply on its next
 * election tick.
 */
export default function SetupPage() {
  const socket = useElectionSocket(true);
  const [placements, setPlacements] = useState<Map<string, ScannerPlacement>>(new Map());
  const [drafts, setDrafts] = useState<Map<string, CalibrationDraft>>(new Map());

  const handlePlace = useCallback(
    (scannerId: string, x: number, y: number) => {
      setPlacements((prev) => {
        const next = new Map(prev);
        next.set(scannerId, { scannerId, x, y });
        return next;
      });
      socket.sendPlaceDevice(scannerId, x, y);
    },
    [socket]
  );

  const draftFor = (id: string): CalibrationDraft =>
    drafts.get(id) ?? {
      rssiAt1m: String(RSSI_AT_1M_DEFAULT),
      pathLossExponent: String(PATH_LOSS_EXPONENT_DEFAULT),
    };

  const setDraft = (id: string, patch: Partial<CalibrationDraft>) => {
    setDrafts((prev) => {
      const next = new Map(prev);
      next.set(id, { ...draftFor(id), ...patch });
      return next;
    });
  };

  const submitCalibration = (id: string) => {
    const draft = draftFor(id);
    const rssiAt1m = Number.parseFloat(draft.rssiAt1m);
    const pathLossExponent = Number.parseFloat(draft.pathLossExponent);
    if (!Number.isFinite(rssiAt1m) || !Number.isFinite(pathLossExponent)) return;
    // Client-side sanity clamp only; the server re-validates and drops
    // anything out of range regardless.
    const clampedRssi = Math.max(-100, Math.min(0, rssiAt1m));
    const clampedExponent = Math.max(1, Math.min(6, pathLossExponent));
    socket.sendSetCalibration(id, clampedRssi, clampedExponent);
  };

  return (
    <div className="min-h-screen bg-[#0a0f1e] text-slate-100">
      <NavBar />
      <main className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        <header>
          <h1 className="text-lg font-semibold tracking-tight">Setup Wizard</h1>
          <p className="text-xs text-slate-500">
            Drag each known scanner onto the floor plan, then set its RSSI-at-1m and path-loss
            exponent. Manual placement + numeric calibration — the honest, non-guided version.
          </p>
        </header>

        <div className="flex flex-col gap-6 lg:flex-row">
          <section className="rounded-xl border border-white/5 bg-white/[0.02] p-4 backdrop-blur lg:w-[55%]">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
              Floor Plan
            </h2>
            <div className="aspect-square w-full">
              <FloorPlan
                scanners={socket.scanners}
                placements={placements}
                positions={[]}
                owner={socket.owner}
                lastHandoff={null}
                chirp={null}
                rangingEvent={null}
                onPlace={handlePlace}
              />
            </div>
          </section>

          <section className="rounded-xl border border-white/5 bg-white/[0.02] p-4 backdrop-blur lg:w-[45%]">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
              Known Scanners
            </h2>
            {socket.scanners.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-600">
                No scanners reporting yet — a scanner id appears here once it starts reporting to
                the aggregator.
              </p>
            ) : (
              <div className="space-y-3">
                {socket.scanners.map((scanner) => {
                  const placed = placements.get(scanner.id);
                  const draft = draftFor(scanner.id);
                  return (
                    <div
                      key={scanner.id}
                      className="rounded-lg border border-white/5 bg-white/[0.02] p-3"
                    >
                      <div className="mb-2 flex items-center justify-between">
                        <span className="font-mono text-sm text-slate-200">{scanner.id}</span>
                        <span className="text-[10px] uppercase tracking-widest text-slate-500">
                          {placed !== undefined
                            ? `placed @ (${placed.x.toFixed(1)}, ${placed.y.toFixed(1)})`
                            : "unplaced — drag on the floor plan"}
                        </span>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-widest text-slate-500">
                          RSSI @ 1m (dBm)
                          <input
                            type="number"
                            step="0.1"
                            value={draft.rssiAt1m}
                            onChange={(e) => setDraft(scanner.id, { rssiAt1m: e.target.value })}
                            onBlur={() => submitCalibration(scanner.id)}
                            className="rounded-md border border-white/10 bg-[#060911] px-2 py-1.5 font-mono text-xs text-slate-100 focus:border-cyan-400/60 focus:outline-none"
                          />
                        </label>
                        <label className="flex flex-col gap-1 text-[10px] uppercase tracking-widest text-slate-500">
                          Path-loss exponent
                          <input
                            type="number"
                            step="0.1"
                            value={draft.pathLossExponent}
                            onChange={(e) =>
                              setDraft(scanner.id, { pathLossExponent: e.target.value })
                            }
                            onBlur={() => submitCalibration(scanner.id)}
                            className="rounded-md border border-white/10 bg-[#060911] px-2 py-1.5 font-mono text-xs text-slate-100 focus:border-cyan-400/60 focus:outline-none"
                          />
                        </label>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
