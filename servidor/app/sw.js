// Service worker da Carteira Portgiga: guarda a "casca" do app (telas, estilo, código) para abrir rápido.
// Saldo, histórico e envios sempre vêm do servidor (nunca do cache).
const CACHE = 'portgiga-app-v5';
const CASCA = ['/app/', '/app/app.css', '/app/app.js', '/app/qrcode.js', '/app/minerador.js', '/app/manifest.webmanifest',
  '/app/icones/icone-192.png', '/app/icones/icone-512.png', '/app/icones/apple-touch-icon.png', '/app/icones/favicon-32.png', '/app/icones/icone.svg'];
self.addEventListener('install', ev => { ev.waitUntil(caches.open(CACHE).then(c => c.addAll(CASCA))); self.skipWaiting(); });
self.addEventListener('activate', ev => {
  ev.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', ev => {
  const url = new URL(ev.request.url);
  if (ev.request.method !== 'GET' || url.origin !== location.origin || !url.pathname.startsWith('/app/')) return;
  // rede primeiro (pega versão nova), cache se estiver sem internet
  ev.respondWith(fetch(ev.request).then(r => {
    const copia = r.clone(); caches.open(CACHE).then(c => c.put(ev.request, copia)); return r;
  }).catch(() => caches.match(ev.request)));
});
