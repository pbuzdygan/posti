const CACHE_NAME = "posti-shell-precache-v2";
const PRECACHED_RESOURCES = ["/index.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHED_RESOURCES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }

  const request = event.request;

  // Only handle navigation/document requests for offline shell.
  if (
    request.mode === "navigate" ||
    (request.destination === "document" ||
      (request.headers.get("accept") || "").includes("text/html"))
  ) {
    event.respondWith(
      fetch(request).catch(() => caches.match("/index.html"))
    );
  }
  // For all other requests (JS, CSS, images, API), let the browser/network
  // handle them normally so we don't ever return HTML in place of scripts.
});
