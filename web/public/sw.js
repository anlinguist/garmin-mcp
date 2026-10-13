// Rabbit service worker: Web Push only (no offline caching in v1).
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('push', (event) => {
  let data = {}
  try { data = event.data ? event.data.json() : {} } catch { data = {} }
  const title = typeof data.title === 'string' ? data.title : 'Rabbit'
  const body = typeof data.body === 'string' ? data.body : ''
  const url = typeof data.url === 'string' && data.url.startsWith('/app/') ? data.url : '/app/'
  event.waitUntil(self.registration.showNotification(title, {
    body,
    icon: '/app/icons/icon-192.png',
    badge: '/app/icons/icon-192.png',
    data: { url },
  }))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = event.notification.data?.url || '/app/'
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    for (const w of wins) {
      // The app is already open (maybe in another chat): tell it where to go rather than
      // navigating, which iOS Home Screen apps don't reliably do.
      if (new URL(w.url).pathname.startsWith('/app/')) {
        await w.focus()
        w.postMessage({ type: 'open', url })
        return
      }
    }
    await self.clients.openWindow(url)
  })())
})
