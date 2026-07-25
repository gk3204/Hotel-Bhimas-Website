/* Hotel Bhimas staff PWA service worker (prompt 13).
 * Online-first: never intercepts writes or the cross-origin backend API. It only
 * provides an installable, offline-loadable app shell (static assets + SPA fallback).
 * Write actions (start/finish cleaning, raise ticket, restock) go straight to the
 * network — there is no offline write outbox in this build (see PROJECT_STATE / plan).
 */
const CACHE = "bhimas-staff-v1";
const SHELL = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/android-chrome-192x192.png",
  "/android-chrome-512x512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return; // never cache POST/PUT — writes stay online-only
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // ignore the backend API (different origin)

  // SPA navigations: network-first, fall back to the cached shell when offline.
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/index.html")));
    return;
  }

  // Static assets (Vite build output): cache-first, then network (and cache it).
  e.respondWith(
    caches.match(req).then(
      (cached) =>
        cached ||
        fetch(req)
          .then((resp) => {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
            return resp;
          })
          .catch(() => cached)
    )
  );
});
