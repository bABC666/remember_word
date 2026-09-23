import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

const json = (value: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }))

/** The endpoints the shell reads once it is past the sign-in gate. */
function signedInRoutes(url: string, overrides: Record<string, unknown> = {}) {
  if (url.includes('/api/auth/me')) {
    return json({
      id: 1, username: 'admin', display_name: 'admin', role: 'admin', is_admin: true,
      settings: { daily_new_words: 15, article_length: 650, onboarding_seen: true },
    })
  }
  if (url.includes('/api/settings/onboarding')) return json({ seen: true })
  if (url.endsWith('/api/auth/sessions')) return json({ sessions: [] })
  if (url.endsWith('/api/settings')) return json(settingsPayload())
  if (url.endsWith('/api/dashboard')) {
    return json(overrides.dashboard ?? { today_new: 0, due_reviews: 0, weak_words: 0, reading_status: 'not_generated', streak_days: 0 })
  }
  if (url.endsWith('/api/imports')) return json([])
  return json({ total: 0, words: [] })
}

function settingsPayload() {
  return {
    deepseek_api_key_configured: false, deepseek_api_key_masked: '', deepseek_base_url: 'https://api.deepseek.com',
    deepseek_model: 'deepseek-flash', deepseek_model_display: 'DeepSeek V4.1-Flash', daily_new_words: 15,
    article_length: 650, ocr_language: 'en', ocr_use_gpu: false, paddleocr_available: false,
    paddleocr_message: '未安装', data_directory: 'D:/data', database_path: 'D:/data/vocab.db',
    backups_directory: 'D:/data/backups', can_manage_instance_settings: true, onboarding_seen: true,
  }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => signedInRoutes(String(input))))
})

describe('App shell', () => {
  it('navigates through the six-page shell once signed in', async () => {
    render(<App />)
    expect(await screen.findByText('今天也从一个词开始。')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: '设置' }))
    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument()
    expect(screen.getByText(/当前对应：DeepSeek V4\.1-Flash/)).toBeInTheDocument()
  })

  it('shows the signed-in account and a way out', async () => {
    render(<App />)
    expect(await screen.findByText('admin')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '退出登录' })).toBeInTheDocument()
  })

  it('renders a useful empty state instead of a blank study page', async () => {
    window.history.pushState({}, '', '/study')
    render(<App />)
    expect(await screen.findByText('今天暂时没有待学习的单词')).toBeInTheDocument()
  })

  it('accumulates repeated image selections and removes a mistaken file', async () => {
    window.history.pushState({}, '', '/import')
    render(<App />)
    await screen.findByText('今天也从一个词开始。').catch(() => undefined)
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!
    const first = new File(['first'], 'page-one.png', { type: 'image/png', lastModified: 1 })
    const second = new File(['second'], 'page-two.png', { type: 'image/png', lastModified: 2 })

    await userEvent.upload(input, first)
    await userEvent.upload(input, second)

    expect(screen.getByText('page-one.png')).toBeInTheDocument()
    expect(screen.getByText('page-two.png')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建导入批次（2 张）' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '移除 page-one.png' }))
    expect(screen.queryByText('page-one.png')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建导入批次（1 张）' })).toBeInTheDocument()
  })

  it('keeps a permanent help entry and opens first-use guidance', async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/settings/onboarding')) return json({ seen: false })
      return signedInRoutes(url)
    })
    window.history.pushState({}, '', '/')
    render(<App />)

    expect(await screen.findByRole('dialog', { name: '欢迎使用拾词' })).toBeInTheDocument()
    expect(screen.getByText('原书是事实，AI 是助手')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '开始使用' }))
    expect(screen.queryByRole('dialog', { name: '欢迎使用拾词' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '使用说明' }).length).toBeGreaterThan(0)
  })
})
