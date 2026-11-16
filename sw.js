// Kiosk Signage Player – Service Worker
// アプリ本体をキャッシュし、オフラインでも起動できるようにする。
// index.html を更新したら CACHE の番号を上げると、端末側で新しい版に入れ替わる。
const CACHE = 'kiosk-signage-v1.4.0';
const SHELL = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/apple-touch-icon.png',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;

  // 画面（HTML）はネット優先：つながっていれば最新版、圏外ならキャッシュ
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req)
        .then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put('./index.html', copy)); return res; })
        .catch(() => caches.match('./index.html').then(r => r || caches.match('./')))
    );
    return;
  }
  // それ以外（アイコン等）はキャッシュ優先
  e.respondWith(caches.match(req).then(r => r || fetch(req)));
});
