import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ImportPage } from './ImportPage'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('keeps the OCR action pending while a large image runs beyond two minutes', async () => {
  const batch = {
    id: 42, status: 'created', stage: 'upload',
    created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z',
    raw_ocr_text: '', error_stage: '', error_message: '',
    images: [{ id: 1, original_name: 'page.jpg', width: 1279, height: 1706, ocr_text: '', error_message: '', is_deleted: false }],
    candidates: [],
  }
  let finishOcr!: (response: Response) => void
  const ocrSignals: AbortSignal[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === '/api/imports') return Promise.resolve(Response.json([{ id: 42, status: 'created', stage: 'upload', created_at: batch.created_at }]))
    if (path === '/api/imports/42') return Promise.resolve(Response.json(batch))
    if (path === '/api/imports/42/ocr') {
      const ocrSignal = init?.signal as AbortSignal
      ocrSignals.push(ocrSignal)
      return new Promise<Response>((resolve, reject) => {
        finishOcr = resolve
        ocrSignal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    }
    throw new Error(`Unexpected request: ${path}`)
  }))

  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ImportPage /></QueryClientProvider>)
  await userEvent.click(await screen.findByRole('button', { name: /批次 #42/ }))
  await userEvent.click(await screen.findByRole('button', { name: '开始 OCR 识别' }))
  expect(await screen.findByRole('button', { name: '正在 OCR…' })).toBeDisabled()

  vi.useFakeTimers()
  await vi.advanceTimersByTimeAsync(120_000)
  expect(ocrSignals[0].aborted).toBe(false)
  finishOcr(new Response(JSON.stringify({ ...batch, status: 'ocr_complete', stage: 'ocr', raw_ocr_text: 'apple' }), { status: 200 }))
  await vi.runAllTimersAsync()
})
