// Minimal service worker — enables "Install app" without offline gymnastics.
// We deliberately DON'T cache HTML or API responses so users always see
// fresh data; only static assets get a stale-while-revalidate.
const CACHE = "modboard-static-v1";
const STATIC_ASSETS = ["/static/style.css", "/static/manifest.json", "/static/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Drop old caches when the version bumps
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Only intercept GETs to our own /static/*
  if (event.request.method !== "GET" || !url.pathname.startsWith("/static/")) return;

  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(event.request);
      const networkFetch = fetch(event.request)
        .then((resp) => {
          if (resp.ok) cache.put(event.request, resp.clone());
          return resp;
        })
        .catch(() => cached);          // offline: serve cached if any
      return cached || networkFetch;    // first request: wait for network
    })
  );
});
