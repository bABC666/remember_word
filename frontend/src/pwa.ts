/** Register the offline shell only for a production build.
 *
 * The service worker itself is deliberately limited to the app shell and static
 * build assets.  It must never cache an authenticated API response, otherwise
 * switching accounts could reveal stale private data.
 */
export function registerPwaServiceWorker(
  isProduction = import.meta.env.PROD,
  serviceWorker: Pick<ServiceWorkerContainer, 'register'> | undefined = navigator.serviceWorker,
) {
  if (!isProduction || !serviceWorker) return
  void serviceWorker.register('/sw.js').catch(() => undefined)
}
