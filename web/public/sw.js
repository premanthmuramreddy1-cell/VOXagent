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

// Reminders and the daily briefing arrive as Web Push messages, even when the app is closed.
self.addEventListener('push', (event) => {
  let data = { title: 'VoxAgent', body: '', url: '/' }
  try {
    data = { ...data, ...event.data.json() }
  } catch {
    /* plain-text or empty push */
  }
  event.waitUntil(self.registration.showNotification(data.title, {
    body: data.body, icon: '/icon.svg', badge: '/icon.svg', data: { url: data.url }, tag: data.title,
  }))
})
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = event.notification.data?.url || '/'
  event.waitUntil(self.clients.matchAll({ type: 'window' }).then((wins) => {
    const open = wins.find((w) => w.url.startsWith(self.location.origin))
    return open ? open.focus().then((w) => w.navigate(target)) : self.clients.openWindow(target)
  }))
})
