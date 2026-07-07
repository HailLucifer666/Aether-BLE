import { redirect } from "next/navigation";

/** Root route redirects to /spatial, the Phase 10 default/MVP view. The
 * previous single-file demo (Simulation/Live/Mesh source toggle) has been
 * superseded by the routed app under src/app/{spatial,signal-lab,timeline,
 * setup,mesh}; /mesh remains reachable as the untouched fallback viewer. */
export default function RootPage() {
  redirect("/spatial");
}
