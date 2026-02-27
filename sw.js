var CACHE = 'aramaps-v1';
var PRECACHE = [
  '/',
  '/manifest.json',
  'https://unpkg.com/maplibre-gl@5.18.0/dist/maplibre-gl.js',
  'https://unpkg.com/maplibre-gl@5.18.0/dist/maplibre-gl.css'
];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(cache) {
      return cache.addAll(PRECACHE);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE; })
            .map(function(k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function(e) {
  e.respondWith(
    caches.match(e.request).then(function(cached) {
      if (cached) return cached;
      return fetch(e.request).then(function(response) {
        // Cache tile and font requests
        var url = e.request.url;
        if (response.ok && (url.indexOf('.pbf') > -1 || url.indexOf('.ttf') > -1 || url.indexOf('.png') > -1)) {
          var clone = response.clone();
          caches.open(CACHE).then(function(cache) {
            cache.put(e.request, clone);
          });
        }
        return response;
      });
    })
  );
});
