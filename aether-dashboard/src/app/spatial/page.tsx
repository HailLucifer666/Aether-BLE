"use client";

import { useCallback, useState } from "react";
import NavBar from "../components/NavBar";
import FloorPlan, { type ScannerPlacement } from "../components/FloorPlan";
import { useElectionSocket } from "../mesh/useElectionSocket";

/**
 * Spatial View — the Phase 10 MVP/default route. Renders the floor plan,
 * live position dot(s), and the cyan ownership halo. Every visual fact
 * traces back to the socket hook's state (election.owner, election.scanners,
 * positions map) — this component never arbitrates anything locally.
 *
 * Scanner (x, y) placements are not echoed back by any current server
 * message (the wire contract only has placeDevice client->server), so this
 * view keeps an optimistic client-side cache of "where I told the server to
 * place each icon" purely for rendering continuity within the session — not
 * an ownership/arbitration decision, and it is overwritten the moment a
 * fresh drag sends a new placeDevice.
 */
export default function SpatialPage() {
  const socket = useElectionSocket(true);
  const [placements, setPlacements] = useState<Map<string, ScannerPlacement>>(new Map());

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

  const positions = Array.from(socket.positions.entries()).map(([userId, p]) => ({
    userId,
    ...p,
  }));

  return (
    <div className="min-h-screen bg-[#0a0f1e] text-slate-100">
      <NavBar />
      <main className="mx-auto max-w-6xl space-y-4 px-4 py-6">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Spatial View</h1>
            <p className="text-xs text-slate-500">
              Drag a scanner icon to place it. Cyan halo = current owner. Amber dot = live
              position.
            </p>
          </div>
          <ConnectionPill state={socket.connection} />
        </header>

        {socket.connection === "offline" && (
          <p className="rounded-lg border border-red-400/30 bg-red-400/5 p-4 text-sm text-red-300">
            Not connected to the mesh aggregator. Start the aggregator and this view will connect
            automatically.
          </p>
        )}

        <section className="rounded-xl border border-white/5 bg-white/[0.02] p-4 backdrop-blur">
          <div className="aspect-square w-full max-w-[720px]">
            <FloorPlan
              scanners={socket.scanners}
              placements={placements}
              positions={positions}
              owner={socket.owner}
              lastHandoff={null}
              chirp={socket.chirp}
              rangingEvent={socket.rangingEvent}
              onPlace={handlePlace}
            />
          </div>
          {socket.scanners.length === 0 && (
            <p className="mt-3 text-center text-sm text-slate-600">
              No scanners reporting yet — waiting for the aggregator.
            </p>
          )}
          {positions.length === 0 && socket.scanners.length > 0 && (
            <p className="mt-3 text-center text-sm text-slate-600">
              No active position track yet — the floor plan will show a live dot once the fusion
              tracker has a fix.
            </p>
          )}
        </section>
      </main>
    </div>
  );
}

function ConnectionPill({ state }: { state: "connecting" | "live" | "offline" }) {
  const label = state === "live" ? "LIVE" : state === "connecting" ? "CONNECTING" : "NOT CONNECTED";
  const cls =
    state === "live"
      ? "border-cyan-400/60 bg-cyan-400/10 text-cyan-300"
      : state === "connecting"
        ? "border-slate-600 bg-slate-800 text-slate-300"
        : "border-red-400/60 bg-red-400/10 text-red-300";
  return (
    <span className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest ${cls}`}>
      {label}
    </span>
  );
}
