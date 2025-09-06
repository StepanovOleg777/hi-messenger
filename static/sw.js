const CACHE_NAME = 'hi-messenger-v1';
const urlsToCache = [
  '/hi',
  '/static/index.html',
  '/static/styles.css',
  '/static/app.js'
];

// Установка Service Worker
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

// Обработка запросов
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => response || fetch(event.request))
  );
});