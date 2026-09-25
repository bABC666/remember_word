import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { registerPwaServiceWorker } from './pwa'

const worker = readFileSync('public/sw.js', 'utf-8')
const styles = readFileSync('src/styles.css', 'utf-8')
const shell = readFileSync('index.html', 'utf-8')
const manifest = JSON.parse(readFileSync('public/manifest.webmanifest', 'utf-8'))

/** Width and height straight out of the PNG IHDR chunk: no image library needed. */
function pngSize(path: string) {
  const header = readFileSync(path).subarray(0, 24)
  expect(header.subarray(1, 4).toString('ascii')).toBe('PNG')
  return [header.readUInt32BE(16), header.readUInt32BE(20)]
}

describe('PWA registration', () => {
  it('registers the worker only for production builds', () => {
    const register = vi.fn().mockResolvedValue({})
    const serviceWorker = { register }

    registerPwaServiceWorker(false, serviceWorker)
    expect(register).not.toHaveBeenCalled()

    registerPwaServiceWorker(true, serviceWorker)
    expect(register).toHaveBeenCalledWith('/sw.js')
  })

  it('never lets a failed registration reach the application', async () => {
    const register = vi.fn().mockReturnValue(Promise.reject(new Error('offline')))

    registerPwaServiceWorker(true, { register })
    await Promise.resolve()

    expect(register).toHaveBeenCalledWith('/sw.js')
  })

  it('does nothing when the browser has no service worker support', () => {
    expect(() => registerPwaServiceWorker(true, undefined)).not.toThrow()
  })
})

describe('offline shell boundary', () => {
  const firstRespondWith = worker.indexOf('event.respondWith')

  it('leaves every /api request on the network path', () => {
    const guard = worker.indexOf('isPrivateApiRequest(url)) return')

    expect(worker).toContain("url.pathname === '/api'")
    expect(worker).toContain("url.pathname.startsWith('/api/')")
    expect(guard).toBeGreaterThan(-1)
    // The guard has to run before any cache or fallback response is produced.
    expect(guard).toBeLessThan(firstRespondWith)
  })

  it('keeps write requests, other origins and non-static paths untouched', () => {
    expect(worker).toContain("request.method !== 'GET'")
    expect(worker).toContain('url.origin !== self.location.origin')
    expect(worker).toContain("url.pathname.startsWith('/assets/')")
    expect(worker).toContain("url.pathname.startsWith('/icons/')")
    expect(worker).toContain("url.pathname === '/manifest.webmanifest'")
  })

  it('caches a response only after the static-resource guard', () => {
    const guard = worker.indexOf('if (!isCacheableStaticResource(url)) return')
    const put = worker.indexOf('cache.put(request')

    expect(guard).toBeGreaterThan(-1)
    expect(put).toBeGreaterThan(guard)
    expect(worker.match(/cache\.put\(/g)).toHaveLength(1)
  })

  it('drops earlier shell versions on activate', () => {
    expect(worker).toContain("const CACHE_NAME = 'shici-shell-v1.2.0'")
    expect(worker).toContain("name.startsWith('shici-shell-') && name !== CACHE_NAME")
    expect(worker).toContain('caches.delete(name)')
  })
})

describe('installable shell metadata', () => {
  it('installs as a standalone app from the site root', () => {
    expect(manifest.display).toBe('standalone')
    expect(manifest.start_url).toBe('/')
    expect(manifest.scope).toBe('/')
    expect(manifest.name).toBe('拾词')
    expect(manifest.theme_color).toBe('#faf9f6')
  })

  it('ships dedicated install icons instead of the desktop launcher image', () => {
    expect(manifest.icons).toEqual([
      expect.objectContaining({ src: '/icons/shici-192.png', sizes: '192x192' }),
      expect.objectContaining({ src: '/icons/shici-512.png', sizes: '512x512' }),
    ])
    expect(pngSize('public/icons/shici-192.png')).toEqual([192, 192])
    expect(pngSize('public/icons/shici-512.png')).toEqual([512, 512])
    expect(pngSize('public/icons/shici-180.png')).toEqual([180, 180])
  })

  it('declares the iOS metadata the standalone shell needs', () => {
    expect(shell).toContain('viewport-fit=cover')
    expect(shell).toContain('apple-mobile-web-app-capable')
    expect(shell).toContain('rel="manifest" href="/manifest.webmanifest"')
    expect(shell).toContain('rel="apple-touch-icon" sizes="180x180" href="/icons/shici-180.png"')
  })
})

describe('mobile layout contract', () => {
  it('keeps fixed controls above the device safe area', () => {
    expect(styles).toContain('--safe-area-bottom: env(safe-area-inset-bottom, 0px)')
    expect(styles).toContain('padding-bottom: calc(68px + var(--safe-area-bottom))')
    expect(styles).toContain('height: calc(64px + var(--safe-area-bottom))')
    expect(styles).toContain('bottom: calc(78px + var(--safe-area-bottom))')
  })

  it('keeps the uploaded image list usable on a phone', () => {
    // F-3: the ≤900px import flow is one column, and the list of saved photos
    // stays visible so a phone can still review, add and remove images.
    expect(styles).not.toContain('.image-list { display: none')
    expect(styles).toContain('.import-progress { grid-template-columns: 1fr; }')
  })

  it('hides the word detail panel only inside the library layout', () => {
    // F-2: `/library/:wordStateId` is the mobile detail route, so the inline
    // panel is hidden but the standalone page must stay visible.
    const hides = styles.match(/\.word-detail \{ display: none/g)

    expect(hides).toHaveLength(1)
    expect(styles).toContain('.library-layout > .word-detail { display: none; }')
    expect(styles).toContain('.library-detail-page .word-detail { display: block; }')
  })
})
