import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('API request timeout', () => {
  it('allows an OCR request to continue after the ordinary two-minute limit', async () => {
    vi.useFakeTimers()
    let complete!: (response: Response) => void
    const fetchMock = vi.fn((_path: string, init: RequestInit) => new Promise<Response>((resolve, reject) => {
      complete = resolve
      init.signal?.addEventListener('abort', () => reject(init.signal?.reason))
    }))
    vi.stubGlobal('fetch', fetchMock)

    const request = api<{ status: string }>('/api/imports/42/ocr', { method: 'POST' }, { timeoutMs: 600_000 })
    await vi.advanceTimersByTimeAsync(120_000)
    expect(fetchMock.mock.calls[0][1].signal?.aborted).toBe(false)

    complete(new Response(JSON.stringify({ status: 'ocr_done' }), { status: 200 }))
    await expect(request).resolves.toEqual({ status: 'ocr_done' })
  })

  it('explains a timed-out request instead of leaking the browser abort error', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_path: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('signal is aborted without reason', 'AbortError')))
    })))

    const request = api('/api/imports/42/ocr', { method: 'POST' }, { timeoutMs: 600_000 })
    const result = expect(request).rejects.toMatchObject({
      name: 'Error',
      message: expect.stringContaining('超时'),
      status: 0,
    })
    await vi.advanceTimersByTimeAsync(600_000)
    await result
  })
})
