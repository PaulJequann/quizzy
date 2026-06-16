const CACHE_NAME = "quizzy-pwa-v1";
const OFFLINE_URL = "/offline";
const STATIC_ASSETS = [
  OFFLINE_URL,
  "/static/css/app.css?v=20260614-tailwind-v4-important-pages",
  "/static/js/pwa-register.js?v=20260615-pwa",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
  "/static/icons/apple-touch-icon.png",
  "/static/icons/favicon.ico",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((cacheNames) =>
        Promise.all(
          cacheNames
            .filter((cacheName) => cacheName !== CACHE_NAME)
            .map((cacheName) => caches.delete(cacheName)),
        ),
      ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const requestUrl = new URL(request.url);

  if (request.method !== "GET" || requestUrl.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (requestUrl.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirst(request));
  }
});

function cacheFirst(request) {
  return caches.match(request).then((cachedResponse) => {
    if (cachedResponse) return cachedResponse;
    return fetch(request).then((networkResponse) => {
      if (networkResponse.ok) {
        const responseCopy = networkResponse.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, responseCopy));
      }
      return networkResponse;
    });
  });
}
