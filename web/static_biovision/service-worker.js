const CACHE_NAME = 'biovision-v19';
const STATIC_ASSETS = [
  '/biovision/',
  '/biovision/identificar',
  '/biovision/air',
  '/static_biovision/css/style.css',
  '/static_biovision/js/app.js',
  '/static_biovision/icons/logo_biovision.png',
  '/static_biovision/icons/biovision_home_background.png',
  '/static_biovision/icons/biovision_hero_white.png',
  '/static_biovision/icons/logo_biovision_air.png',
  '/static_biovision/icons/biovision_air_hero_definitivo.png',
  '/static_biovision/icons/biovision_air_background_desktop_v2.png',
  '/static_biovision/icons/biovision_air_background_mobile_v2.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => Promise.all(
        STATIC_ASSETS.map(asset => cache.add(asset).catch(() => null))
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/biovision/jobs/')) return;

  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request))
  );
});
