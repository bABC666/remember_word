import { render, screen, within } from '@testing-library/react'
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
  window.history.replaceState({}, '', '/')
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => signedInRoutes(String(input))))
})

describe('App shell', () => {
  it('keeps a chosen lexicon after leaving study through home and the navigation', async () => {
    let selected: number | null = null
    const requests: string[] = []
    const word = (name: string, id: number, lexiconId: number) => ({
      id: null, word_state_id: id, legacy_word_id: null, lexicon_entry_id: id,
      lexicon_id: lexiconId, word: name, phonetic: '', part_of_speech: '',
      source_meanings: [], source_raw: '', concise_meanings: [], anchor: '', semantic_note: '',
      status: 'new', first_seen: '', last_review: null, next_review_at: null,
      recall_success: 0, recall_fail: 0, context_exposure: 0, possible_issue: false, notes: '',
    })
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/lexicons') return json([
        { id: 2, name: 'A', description: '', entry_count: 1, is_system: false, source_type: 'user_file', enabled: true },
        { id: 3, name: 'B', description: '', entry_count: 1, is_system: false, source_type: 'user_file', enabled: true },
      ])
      if (url === '/api/lexicons/selection') return json({ lexicon_id: selected, source: selected ? 'explicit' : 'none' })
      if (url === '/api/lexicons/2/select') { selected = 2; return json({ lexicon_id: 2 }) }
      if (url.startsWith('/api/study/today')) {
        const scope = new URL(url, 'http://testserver').searchParams.get('lexicon_id')
        const words = scope === '2' || (!scope && selected === 2)
          ? [word('apple', 11, 2)] : [word('apple', 11, 2), word('banana', 12, 3)]
        return json({ total: words.length, words })
      }
      return signedInRoutes(url)
    })
    window.history.pushState({}, '', '/lexicons')
    render(<App />)
    await userEvent.click((await screen.findAllByRole('button', { name: '学习这个词库' }))[0])
    expect(await screen.findByRole('heading', { name: 'apple' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: '今日概览' }))
    await userEvent.click(await screen.findByRole('link', { name: /开始今日学习/ }))
    expect(await screen.findByRole('heading', { name: 'apple' })).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: '今日概览' }))
    await userEvent.click(screen.getByRole('link', { name: '今日学习' }))
    expect(await screen.findByRole('heading', { name: 'apple' })).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(requests.filter((url) => url.startsWith('/api/study/today')).length).toBeGreaterThanOrEqual(2)
  })

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

  it('reaches the recorded sources and licences from the help dialog', async () => {
    // The display design's third entry point: one line in the help dialog.
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/lexicons')) return json([])
      return signedInRoutes(url)
    })
    window.history.pushState({}, '', '/')
    render(<App />)

    const [helpEntry] = await screen.findAllByRole('button', { name: '使用说明' })
    await userEvent.click(helpEntry)
    const dialog = await screen.findByRole('dialog', { name: '欢迎使用拾词' })

    const link = within(dialog).getByRole('link', { name: '数据来源与许可' })
    expect(link).toHaveAttribute('href', '/sources')

    // What the line may say: the record is a transcript. What it may not say: that
    // anybody verified it, least of all that a licence has been granted.
    const text = dialog.textContent ?? ''
    expect(text).toMatch(/来源/)
    for (const claim of ['已获授权', '符合 CC 要求', '官方授权']) {
      expect(text, claim).not.toContain(claim)
    }
    expect(text).toContain('不代表授权已获确认')

    await userEvent.click(link)
    expect(await screen.findByRole('heading', { name: '数据来源与许可' })).toBeInTheDocument()
    // Navigating away dismisses the dialog rather than leaving it over the page.
    expect(screen.queryByRole('dialog', { name: '欢迎使用拾词' })).not.toBeInTheDocument()
  })
})
