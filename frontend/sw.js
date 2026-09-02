/* =============================================================
   JARVIS — Service Worker (PWA local-first)
   Navegação sempre na rede (HTML sempre atual); estáticos cache-first;
   API sempre na rede.
   ============================================================= */
const CACHE = "jarvis-v20";
const STATIC_ASSETS = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icons/favicon.svg",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-maskable-512.png",
  "./icons/apple-touch-icon.png",
  "./css/tokens.css",
  "./css/base.css",
  "./css/layout.css",
  "./css/components.css?v=19",
  "./css/ops.css?v=19",
  "./js/speech.js?v=13",
  "./js/ops.js?v=18",
  "./js/markdown.js?v=15",
  "./js/app.js?v=18",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // API: sempre rede (nunca servir dado de chat desatualizado).
  if (url.pathname.startsWith("/api")) return;

  // Navegação: rede primeiro, cache como fallback (HTML sempre atualizado).
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() =>
          caches.match(request).then((cached) => cached || caches.match("./index.html"))
        )
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, clone));
        }
        return response;
      });
    })
  );
});