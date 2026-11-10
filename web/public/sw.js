// Caches the app shell so the installed app opens offline. API calls always go to the network.
const SHELL = 'vox-shell-v1'
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  const isShell = event.request.method === 'GET' && url.origin === location.origin &&
    (event.request.mode === 'navigate' || /\.(js|css|svg|webmanifest)$/.test(url.pathname))
  if (!isShell) return
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone()
        caches.open(SHELL).then((cache) => cache.put(event.request, copy))
        return response
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match('/'))),
  )
})
