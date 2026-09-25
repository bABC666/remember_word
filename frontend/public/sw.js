/*
 * The PWA is an offline application shell, never an offline copy of private
 * vocabulary data.  API calls are intentionally left to the browser network
 * path and every cached resource is public, immutable build output.
 */
const CACHE_NAME = 'shici-shell-v1.2.0'
const SHELL_URLS = ['/', '/manifest.webmanifest', '/icons/shici-192.png']

/*
 * Hard boundary: nothing under /api is ever read from, written to or answered
 * from a cache.  Every ordinary and alternate path spelling is matched, so the
 * worker can never replay another account's private data after a user switch.
 */
function isPrivateApiRequest(url) {
  return url.pathname === '/api' || url.pathname.startsWith('/api/')
}

function isCacheableStaticResource(url) {
  return (
    url.pathname.startsWith('/assets/') ||
    url.pathname.startsWith('/icons/') ||
    url.pathname === '/manifest.webmanifest'
  )
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names
        .filter((name) => name.startsWith('shici-shell-') && name !== CACHE_NAME)
        .map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin || isPrivateApiRequest(url)) return

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/')))
    return
  }

  if (!isCacheableStaticResource(url)) return
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (response.ok && response.type === 'basic') {
        void caches.open(CACHE_NAME).then((cache) => cache.put(request, response.clone()))
      }
      return response
    })),
  )
})
