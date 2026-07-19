// BulkDownloader service worker (Phase 108).
//
// Strategy: cache-first for the static shell + same-origin assets,
// network-first for /api/* (data must be fresh), no caching of POST.
//
// We deliberately do NOT precache index.html — it's mostly dynamic
// (CSRF token + bootstrap state are inlined at render time). Instead
// we cache the immutable static assets so the shell loads instantly
// after the first visit, and the network fetch populates the dynamic
// HTML on top.
//
// Version is bumped on every release that touches this file. Old
// caches are wiped on activate.

const VERSION = 'bd-v3.43.80-1';
const STATIC_CACHE = `bd-static-${VERSION}`;
const RUNTIME_CACHE = `bd-runtime-${VERSION}`;

// Hand-picked assets — the minimum needed to show the shell offline.
// app.js + app.css are big enough that pre-caching them is worth the
// install-time cost; smaller assets pull in on first-use.
const PRECACHE = [
  '/static/app.css',
  '/static/app.js',
  '/static/components.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      // addAll fails the whole install if any one URL 404s. We use
      // individual adds so a missing icon doesn't break PWA install.
      Promise.all(PRECACHE.map((url) =>
        cache.add(url).catch((err) => console.warn('[sw] precache miss', url, err))
      ))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => !k.endsWith(VERSION) && k.startsWith('bd-'))
            .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Never cache state-mutating requests
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Skip cross-origin entirely — let the browser handle them normally
  if (url.origin !== self.location.origin) return;

  // /api/* and SSE go straight to network. /api/openapi.json could be
  // cached but it's tiny and we want fresh after route additions.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/sse/')) {
    event.respondWith(
      fetch(req).catch(() =>
        // Offline: return a structured "no network" error so the JS
        // can show a sensible "offline" badge instead of a generic
        // TypeError. 503 keeps the error semantics close to HTTP.
        new Response(JSON.stringify({error: 'offline', offline: true}),
          {status: 503, headers: {'Content-Type': 'application/json'}})
      )
    );
    return;
  }

  // Static assets: cache-first, fall back to network, update cache in
  // background for next time.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const fetchPromise = fetch(req).then((resp) => {
          if (resp && resp.status === 200) {
            const respClone = resp.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, respClone));
          }
          return resp;
        }).catch(() => cached);  // offline: just use the cache
        return cached || fetchPromise;
      })
    );
    return;
  }

  // / and other HTML routes: network-first (dynamic CSRF), cache as
  // last-resort fallback. Cached shell is fine for offline display
  // but live data won't be there.
  event.respondWith(
    fetch(req).then((resp) => {
      if (resp && resp.status === 200 && req.method === 'GET') {
        const respClone = resp.clone();
        caches.open(RUNTIME_CACHE).then((c) => c.put(req, respClone));
      }
      return resp;
    }).catch(() => caches.match(req).then((cached) =>
      cached || new Response('Offline — and no cached copy available.',
        {status: 503, headers: {'Content-Type': 'text/plain'}})
    ))
  );
});

// Listen for SKIP_WAITING from the page so the operator can refresh
// without waiting for the natural activation cycle.
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
