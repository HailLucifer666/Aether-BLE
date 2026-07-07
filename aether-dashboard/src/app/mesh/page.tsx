"use client";

import NavBar from "../components/NavBar";
import MeshView from "./MeshView";
import { useElectionSocket } from "./useElectionSocket";

/**
 * /mesh route — thin wrapper that gives the existing, untouched MeshView
 * component a real App Router route. MeshView.tsx, useElectionSocket.ts,
 * and types.ts under this folder are byte-for-byte unmodified from before
 * Phase 10 (useElectionSocket.ts gained additive fields for the new
 * position/placeDevice/setCalibration/setTuning contract, but every field
 * MeshView.tsx itself consumes is untouched).
 */
export default function MeshPage() {
  const socket = useElectionSocket(true);
  return (
    <div className="min-h-screen bg-[#0a0f1e] text-slate-100">
      <NavBar />
      <main className="mx-auto max-w-3xl px-4 py-6">
        <MeshView {...socket} />
      </main>
    </div>
  );
}
