/* Woody SW — жёсткий сброс кэша при каждой новой версии */
const CACHE = "woody-shell-v074";
self.addEventListener("install", (e) => {
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // API и HTML — всегда из сети
  if (url.pathname.startsWith("/api/") || url.pathname === "/" || url.pathname.endsWith(".html")) {
    e.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }
  // JS/CSS с ?v= — сеть, потом кэш
  if (url.search.includes("v=")) {
    e.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }
  e.respondWith(
    fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(req))
  );
});

self.addEventListener("push", (event) => {
  let data = {}; try { data = event.data ? event.data.json() : {}; } catch (_) { data = { body: event.data ? event.data.text() : "Новое сообщение" }; }
  event.waitUntil(self.registration.showNotification(data.title || "Woody", { body: data.body || "Новое сообщение", icon: "/assets/icon-192.png", badge: "/assets/favicon-128.png", tag: data.tag || "woody-message", data: { url: data.url || "/" } }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close(); const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => { for (const client of clients) { if (client.url.startsWith(self.location.origin) && "focus" in client) return client.focus(); } return self.clients.openWindow(target); }));
});
