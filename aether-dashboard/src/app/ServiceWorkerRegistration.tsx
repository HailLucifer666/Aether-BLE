"use client";

import { useEffect } from "react";

/** Registers the app-shell-only service worker (public/sw.js) once on
 * mount. Client-side registration is required under the App Router since
 * layout.tsx itself is a server component. */
export default function ServiceWorkerRegistration() {
  useEffect(() => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Best-effort; the dashboard works fully without the PWA shell cache.
    });
  }, []);

  return null;
}
