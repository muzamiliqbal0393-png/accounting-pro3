/* ACCOUNTING PRO service worker - offline shell caching */
const CACHE = "accounting-pro-v1";
const ASSETS = [
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/js/charts.js",
  "/static/vendor/bootstrap.min.css",
  "/static/vendor/bootstrap.bundle.min.js",
  "/static/vendor/chart.umd.min.js",
  "/static/manifest.json"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(ASSETS).catch(function () {});
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;

  // Static assets: cache first
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then(function (cached) {
        return cached || fetch(request).then(function (response) {
          const copy = response.clone();
          caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
          return response;
        });
      })
    );
    return;
  }

  // Pages: network first with offline fallback
  event.respondWith(
    fetch(request).catch(function () {
      return caches.match(request).then(function (cached) {
        return cached || new Response(
          "<h1 style='font-family:sans-serif'>You are offline</h1>" +
          "<p style='font-family:sans-serif'>ACCOUNTING PRO needs a connection to load accounts and reports.</p>",
          { headers: { "Content-Type": "text/html" } }
        );
      });
    })
  );
});
