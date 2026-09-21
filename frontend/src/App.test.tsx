import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }))

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/api/dashboard')) return json({ today_new: 0, due_reviews: 0, weak_words: 0, reading_status: 'not_generated', streak_days: 0 })
    if (url.includes('/api/settings')) return json({ deepseek_api_key_configured: false, deepseek_api_key_masked: '', deepseek_base_url: 'https://api.deepseek.com', deepseek_model: 'deepseek-chat', daily_new_words: 15, article_length: 650, ocr_language: 'en', ocr_use_gpu: false, paddleocr_available: false, paddleocr_message: '未安装', data_directory: 'D:/data', database_path: 'D:/data/vocab.db', backups_directory: 'D:/data/backups' })
    return json({ total: 0, words: [] })
  }))
})

describe('App', () => {
  it('navigates through the six-page shell', async () => {
    render(<App />)
    expect(await screen.findByText('今天也从一个词开始。')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: '设置' }))
    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument()
  })

  it('renders a useful empty state instead of a blank study page', async () => {
    window.history.pushState({}, '', '/study')
    render(<App />)
    expect(await screen.findByText('今天暂时没有待学习的单词')).toBeInTheDocument()
  })
})
