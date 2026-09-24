import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { registerPwaServiceWorker } from './pwa'

describe('PWA registration', () => {
  it('registers the worker only for production builds', () => {
    const register = vi.fn().mockResolvedValue({})
    const serviceWorker = { register }

    registerPwaServiceWorker(false, serviceWorker)
    expect(register).not.toHaveBeenCalled()

    registerPwaServiceWorker(true, serviceWorker)
    expect(register).toHaveBeenCalledWith('/sw.js')
  })

  it('keeps the worker outside the API and write-request paths', () => {
    const worker = readFileSync('public/sw.js', 'utf-8')

    expect(worker).toContain("url.pathname.startsWith('/api/')")
    expect(worker).toContain("request.method !== 'GET'")
  })

  it('declares compact install icons instead of the desktop launcher image', () => {
    const manifest = JSON.parse(readFileSync('public/manifest.webmanifest', 'utf-8'))

    expect(manifest.icons).toEqual([
      expect.objectContaining({ src: '/icons/shici-192.png', sizes: '192x192' }),
      expect.objectContaining({ src: '/icons/shici-512.png', sizes: '512x512' }),
    ])
  })

  it('keeps fixed mobile controls above the device safe area', () => {
    const styles = readFileSync('src/styles.css', 'utf-8')

    expect(styles).toContain('--safe-area-bottom: env(safe-area-inset-bottom, 0px)')
    expect(styles).toContain('padding-bottom: calc(68px + var(--safe-area-bottom))')
    expect(styles).toContain('height: calc(64px + var(--safe-area-bottom))')
    expect(styles).toContain('bottom: calc(78px + var(--safe-area-bottom))')
  })
})
