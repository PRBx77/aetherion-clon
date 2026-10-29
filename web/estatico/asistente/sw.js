/* Service worker de Aether: permite instalar la app y abrirla sin cobertura.
   Nunca guarda conversaciones: las llamadas a /api/ no pasan por la caché. */
const CACHE = "aether-v1";
const BASE = ["/asistente", "/static/asistente/asistente.css", "/static/asistente/asistente.js",
  "/static/asistente/icono-192.png", "/static/asistente/icono-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  if (u.pathname === "/asistente") {
    // página: primero la red (token de sesión fresco); sin cobertura, la última copia
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok && !r.redirected) { const c = r.clone(); caches.open(CACHE).then(k => k.put("/asistente", c)); }
      return r;
    }).catch(() => caches.match("/asistente")));
    return;
  }
  if (u.pathname.startsWith("/static/asistente/")) {
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const c = r.clone(); caches.open(CACHE).then(k => k.put(e.request, c)); }
      return r;
    }).catch(() => caches.match(e.request)));
  }
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then(ws => {
    for (const w of ws) if (w.url.includes("/asistente")) return w.focus();
    return self.clients.openWindow("/asistente");
  }));
});
