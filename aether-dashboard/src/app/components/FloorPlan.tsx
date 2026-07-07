"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Radio } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { ChirpInfo, HandoffInfo, RangingEvent, ScannerEntry } from "../mesh/types";

// Fixed floor-plan scale: 40 pixels per meter. The SVG viewBox is
// FLOOR_PLAN_METERS_SQUARE meters on a side, centered on (0,0), so a scanner
// placed at server (x, y) = (0, 0) renders at the plan's center.
export const PIXELS_PER_METER = 40;
export const FLOOR_PLAN_METERS_SQUARE = 12; // 12m x 12m visible area
export const FLOOR_PLAN_PX = FLOOR_PLAN_METERS_SQUARE * PIXELS_PER_METER;

const MAX_METERS_ABS = 1000; // matches the server's |x|/|y| <= 1000m bound

function metersToPx(m: number): number {
  return FLOOR_PLAN_PX / 2 + m * PIXELS_PER_METER;
}

function pxToMeters(px: number): number {
  return (px - FLOOR_PLAN_PX / 2) / PIXELS_PER_METER;
}

function clampMeters(m: number): number {
  return Math.max(-MAX_METERS_ABS, Math.min(MAX_METERS_ABS, m));
}

export interface ScannerPlacement {
  scannerId: string;
  x: number;
  y: number;
}

export interface FloorPlanPosition {
  userId: string;
  x: number;
  y: number;
  uncertaintyRadiusM: number;
}

interface FloorPlanProps {
  scanners: ScannerEntry[];
  /** Known placements, keyed by scanner id. A scanner with no entry here has
   * never been placed yet and renders at a default staging slot along the
   * top edge so it's still visible/draggable. */
  placements: Map<string, ScannerPlacement>;
  positions: FloorPlanPosition[];
  owner: string | null;
  lastHandoff: HandoffInfo | null;
  chirp: ChirpInfo | null;
  rangingEvent: RangingEvent | null;
  onPlace: (scannerId: string, x: number, y: number) => void;
}

/** Shared SVG floor plan used by both /spatial and /setup. Purely a
 * renderer: every visual fact (owner, position, uncertainty, handoff,
 * chirp) is a prop traced straight back to the server's last message. Drag
 * gestures only ever produce a `placeDevice` send via onPlace — never a
 * local ownership/ownership-adjacent decision. */
export default function FloorPlan({
  scanners,
  placements,
  positions,
  owner,
  chirp,
  rangingEvent,
  onPlace,
}: FloorPlanProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragPreview, setDragPreview] = useState<{ x: number; y: number } | null>(null);

  const clientToLocal = useCallback((clientX: number, clientY: number): { x: number; y: number } | null => {
    const svg = svgRef.current;
    if (svg === null) return null;
    const rect = svg.getBoundingClientRect();
    const localPxX = ((clientX - rect.left) / rect.width) * FLOOR_PLAN_PX;
    const localPxY = ((clientY - rect.top) / rect.height) * FLOOR_PLAN_PX;
    return { x: clampMeters(pxToMeters(localPxX)), y: clampMeters(pxToMeters(localPxY)) };
  }, []);

  const handlePointerDown = useCallback(
    (scannerId: string) => (e: React.PointerEvent<SVGGElement>) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      setDraggingId(scannerId);
      const local = clientToLocal(e.clientX, e.clientY);
      if (local !== null) setDragPreview(local);
    },
    [clientToLocal]
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<SVGGElement>) => {
      if (draggingId === null) return;
      const local = clientToLocal(e.clientX, e.clientY);
      if (local !== null) setDragPreview(local);
    },
    [draggingId, clientToLocal]
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<SVGGElement>) => {
      if (draggingId === null) return;
      const local = clientToLocal(e.clientX, e.clientY);
      if (local !== null) {
        onPlace(draggingId, local.x, local.y);
      }
      setDraggingId(null);
      setDragPreview(null);
    },
    [draggingId, clientToLocal, onPlace]
  );

  // Default staging slots for scanners never placed yet: spread evenly along
  // the top edge so they stay visible and draggable even before a first
  // placeDevice has been sent.
  const unplaced = scanners.filter((s) => !placements.has(s.id));
  const stagingSlot = (idx: number): { x: number; y: number } => {
    const span = FLOOR_PLAN_METERS_SQUARE * 0.7;
    const start = -span / 2;
    const step = unplaced.length > 1 ? span / (unplaced.length - 1) : 0;
    return { x: start + idx * step, y: -FLOOR_PLAN_METERS_SQUARE / 2 + 1 };
  };

  const isChirpArmed = rangingEvent !== null;

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${FLOOR_PLAN_PX} ${FLOOR_PLAN_PX}`}
      className="h-full w-full touch-none rounded-lg border border-white/5 bg-[#060911]"
      role="img"
      aria-label="Floor plan"
    >
      {/* grid, 1m spacing */}
      <g className="text-white/5" stroke="currentColor" strokeWidth={1}>
        {Array.from({ length: FLOOR_PLAN_METERS_SQUARE + 1 }, (_, i) => (
          <line
            key={`v${i}`}
            x1={i * PIXELS_PER_METER}
            y1={0}
            x2={i * PIXELS_PER_METER}
            y2={FLOOR_PLAN_PX}
          />
        ))}
        {Array.from({ length: FLOOR_PLAN_METERS_SQUARE + 1 }, (_, i) => (
          <line
            key={`h${i}`}
            x1={0}
            y1={i * PIXELS_PER_METER}
            x2={FLOOR_PLAN_PX}
            y2={i * PIXELS_PER_METER}
          />
        ))}
      </g>

      {/* chirp ripple: one-shot expanding rings centered on the chirp
          winner's placed position, armed by rangingEvent (same one-shot
          semantics MeshView already uses). */}
      <AnimatePresence>
        {isChirpArmed && chirp !== null && chirp.winnerId !== null && placements.has(chirp.winnerId) && (
          <ChirpRipple
            key={rangingEvent?.atTick ?? 0}
            cx={metersToPx(placements.get(chirp.winnerId)!.x)}
            cy={metersToPx(placements.get(chirp.winnerId)!.y)}
          />
        )}
      </AnimatePresence>

      {/* placed + staged scanner icons */}
      {scanners.map((scanner) => {
        const placed = placements.get(scanner.id);
        const staged =
          placed === undefined
            ? stagingSlot(unplaced.findIndex((s) => s.id === scanner.id))
            : null;
        const isDraggingThis = draggingId === scanner.id;
        const pos =
          isDraggingThis && dragPreview !== null
            ? dragPreview
            : placed !== undefined
              ? { x: placed.x, y: placed.y }
              : staged!;
        const isOwner = owner === scanner.id;
        const cx = metersToPx(pos.x);
        const cy = metersToPx(pos.y);

        return (
          <g
            key={scanner.id}
            onPointerDown={handlePointerDown(scanner.id)}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            className="cursor-grab active:cursor-grabbing"
          >
            {isOwner && (
              <motion.circle
                layoutId="ownership-halo"
                cx={cx}
                cy={cy}
                r={22}
                fill="none"
                stroke="rgb(34,211,238)"
                strokeWidth={2}
                initial={false}
                animate={{ opacity: [0.9, 0.3, 0.9] }}
                transition={{
                  layout: { type: "spring", stiffness: 200, damping: 22 },
                  opacity: { duration: 1.6, repeat: Infinity, ease: "easeInOut" },
                }}
              />
            )}
            <circle
              cx={cx}
              cy={cy}
              r={16}
              className={
                placed === undefined
                  ? "fill-white/5 stroke-white/20"
                  : isOwner
                    ? "fill-cyan-500/20 stroke-cyan-400"
                    : "fill-white/5 stroke-white/30"
              }
              strokeWidth={1.5}
            />
            <Radio
              x={cx - 8}
              y={cy - 8}
              width={16}
              height={16}
              className={isOwner ? "text-cyan-300" : "text-slate-400"}
            />
            <text
              x={cx}
              y={cy + 28}
              textAnchor="middle"
              className={`select-none text-[10px] font-mono ${isOwner ? "fill-cyan-300" : "fill-slate-400"}`}
            >
              {scanner.id}
              {placed === undefined ? " (unplaced)" : ""}
            </text>
          </g>
        );
      })}

      {/* live position dots, one per active fusion track */}
      {positions.map((p) => {
        const cx = metersToPx(p.x);
        const cy = metersToPx(p.y);
        const uncertaintyPx = p.uncertaintyRadiusM * PIXELS_PER_METER;
        return (
          <g key={p.userId}>
            <circle cx={cx} cy={cy} r={uncertaintyPx} className="fill-amber-400/10 stroke-amber-400/30" strokeWidth={1} />
            <circle cx={cx} cy={cy} r={6} className="fill-amber-400" />
            <text
              x={cx}
              y={cy - uncertaintyPx - 6}
              textAnchor="middle"
              className="select-none fill-amber-300 text-[10px] font-mono"
            >
              {p.userId}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** One-shot expanding-ring chirp ripple, keyed by ranging tick so a fresh
 * chirp always restarts the animation. Mirrors MeshView's ChirpPing visual
 * language (concentric rings, fuchsia-ish red-400 accent per the design
 * system's contest/chirp semantic color). */
function ChirpRipple({ cx, cy }: { cx: number; cy: number }) {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <motion.circle
          key={i}
          cx={cx}
          cy={cy}
          r={8}
          fill="none"
          stroke="rgb(248,113,113)"
          strokeWidth={2}
          initial={{ opacity: 0.9, r: 8 }}
          animate={{ opacity: 0, r: 60 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 1.2, ease: "easeOut", delay: i * 0.25 }}
        />
      ))}
    </>
  );
}
