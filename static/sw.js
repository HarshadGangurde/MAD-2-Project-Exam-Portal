// Minimal service worker: makes the app installable and serves our own files (network first) when offline.
// It deliberately ignores every cross-origin request (Vue, Bootstrap, fonts from the CDNs): a service worker's
// fetch() is subject to the page's Content-Security-Policy (connect-src 'self'), so proxying them would break the page.
// Consequence: offline use shows the cached shell only; the CDN libraries still need a network or the browser cache.
const CACHE = 'emp-shell-v3';

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(['/static/js/app.js', '/static/manifest.json'])));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // never touch API calls or anything that is not ours: the browser handles those itself
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
        return r;
      })
      .catch(() => caches.match(e.request)
        .then((m) => m || caches.match('/'))
        .then((m) => m || Response.error()))
  );
});
