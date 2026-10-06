// CDT Water service worker. Registered at a fixed URL; its bytes change when V changes, which is
// how browsers discover updates. V must match APP_V and the ?v= on app.js in index.html (run ./bump-version.sh N).
const V = '1';
const CACHE = 'cdt-water-v' + V;
const ASSETS = ['./', 'index.html', 'app.js?v=' + V, 'data.json?v=' + V, 'manifest.webmanifest', 'icon-192.png', 'icon-512.png', 'apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => Promise.all(ASSETS.map(a =>
    fetch(a, { cache: 'reload' }).then(r => { if (!r.ok) throw new Error(a + ' ' + r.status); return c.put(a, r) })
  ))).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('cdt-water-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // Navigations: cached index.html (works offline), falling back to network
  if (req.mode === 'navigate') {
    e.respondWith(caches.match('index.html').then(r => r || fetch(req)));
    return;
  }
  // Everything else: cache first, then network (and remember it)
  e.respondWith(caches.match(req).then(r => r || fetch(req).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)) }
    return res;
  })));
});
