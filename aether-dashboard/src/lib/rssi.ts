/**
 * Shared RSSI helpers used by both the existing Simulation/Live views in
 * page.tsx and the new Mesh viewer. Lifted out of page.tsx (rather than
 * exported from it) because Next's app-router type-checks page.tsx as a
 * route module and rejects extra named exports.
 */
export function getBars(rssi: number): number {
  return Math.max(0, Math.min(5, Math.floor((rssi + 90) / 8)));
}
