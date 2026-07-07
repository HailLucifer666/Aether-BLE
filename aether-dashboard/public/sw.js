// Minimal Aether PWA service worker: caches the app shell only. It never
// intercepts or caches the live WebSocket connections (ws://127.0.0.1:8765,
// ws://127.0.0.1:8766) or their data — the dashboard remains a pure live
// viewer with no offline replay of mesh state.
const CACHE_NAME = "aether-shell-v1";
const SHELL_URLS = ["/manifest.json", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never touch WebSocket upgrade requests or cross-origin traffic - only
  // cache same-origin GET requests for the static app shell.
  if (event.request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached !== undefined) return cached;
      return fetch(event.request).catch(() => cached as Response);
    })
  );
});
