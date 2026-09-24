import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { Word } from '../types'

const savedWord: Word = {
  id: null,
  word_state_id: 42,
  legacy_word_id: null,
  lexicon_entry_id: 7,
  lexicon_id: 1,
  word: 'amber',
  phonetic: '/ˈæmbər/',
  part_of_speech: 'n.',
  source_meanings: ['琥珀；一种由古代树脂形成的黄色物质', '琥珀色'],
  source_raw: 'amber n. 琥珀；琥珀色。原书补充说明。',
  anchor: '树脂',
  semantic_note: '在原书语境里指琥珀。',
  status: 'weak',
  first_seen: '2026-09-20T00:00:00Z',
  last_review: '2026-09-23T00:00:00Z',
  next_review_at: null,
  recall_success: 2,
  recall_fail: 1,
  context_exposure: 3,
  possible_issue: false,
  notes: '',
}

const detail = {
  ...savedWord,
  review_history: [{
    id: 9, timestamp: '2026-09-23T08:00:00Z', result: 'know',
    status_before: 'learning', status_after: 'known', source: 'daily',
  }],
  article_exposures: [{
    article_id: 5, context: 'The amber caught the morning light.',
    exposure_count: 3, first_exposed_at: '2026-09-21T00:00:00Z',
    last_exposed_at: '2026-09-23T00:00:00Z',
  }],
}

const me = (id: number) => ({
  id, username: id === 1 ? 'alpha' : 'beta', display_name: id === 1 ? 'alpha' : 'beta',
  role: 'user', is_admin: false,
  settings: { daily_new_words: 15, article_length: 650, onboarding_seen: true },
})

function mobile(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn(() => ({
    matches, media: '(max-width: 900px)', onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })))
}

function mockApi(options: {
  currentUser?: () => number
  detailResponse?: () => Promise<Response>
} = {}) {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    requests.push(url)
    if (url === '/api/auth/me') return Promise.resolve(Response.json(me(options.currentUser?.() ?? 1)))
    if (url === '/api/auth/logout') return Promise.resolve(Response.json({ ok: true }))
    if (url === '/api/auth/login') return Promise.resolve(Response.json(me(options.currentUser?.() ?? 1)))
    if (url === '/api/settings/onboarding') return Promise.resolve(Response.json({ seen: true }))
    if (url.startsWith('/api/words?')) return Promise.resolve(Response.json({ total: 1, words: [savedWord] }))
    if (url === '/api/words/state/42') {
      if (options.detailResponse) return options.detailResponse()
      return Promise.resolve(Response.json(detail))
    }
    throw new Error('Unexpected request: ' + url)
  }))
  return requests
}

beforeEach(() => {
  window.history.replaceState({}, '', '/library')
  mobile(true)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('mobile library detail', () => {
  it('opens an article-added word by state id and restores filters, scroll and focus on return', async () => {
    const requests = mockApi()
    render(<App />)
    const search = await screen.findByRole('textbox', { name: '搜索单词' })
    await userEvent.type(search, 'amber')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'weak')
    await userEvent.click(screen.getByRole('button', { name: '最近加入' }))

    const list = document.querySelector<HTMLElement>('.word-list')!
    list.scrollTop = 135
    const wordLink = await screen.findByRole('link', { name: /amber/ })
    await userEvent.click(wordLink)

    expect(window.location.pathname).toBe('/library/42')
    expect(requests).toContain('/api/words/state/42')
    expect(requests).not.toContain('/api/words/42')
    expect(await screen.findByText('琥珀；一种由古代树脂形成的黄色物质')).toBeInTheDocument()
    expect(screen.getByText(/ˈæmbər/)).toBeInTheDocument()
    expect(screen.getByText('The amber caught the morning light.')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: '复习历史' })).getByText(/learning → known/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: '返回词库' }))
    expect(await screen.findByRole('textbox', { name: '搜索单词' })).toHaveValue('amber')
    expect(screen.getByRole('combobox', { name: '学习状态' })).toHaveValue('weak')
    expect(screen.getByRole('button', { name: '最近加入' })).toHaveAttribute('aria-pressed', 'true')
    expect(window.location.search).toContain('search=amber')
    await waitFor(() => {
      expect(document.querySelector<HTMLElement>('.word-list')!.scrollTop).toBe(135)
      expect(screen.getByRole('link', { name: /amber/ })).toHaveFocus()
    })
  })

  it('keeps desktop selection in the existing two-column page', async () => {
    mobile(false)
    mockApi()
    render(<App />)
    const row = await screen.findByRole('button', { name: /amber/ })
    await userEvent.click(row)
    expect(window.location.pathname).toBe('/library')
    expect(await screen.findByText('琥珀；一种由古代树脂形成的黄色物质')).toBeInTheDocument()
  })

  it('shows an owned-word-safe 404 on a direct detail visit', async () => {
    window.history.replaceState({}, '', '/library/42')
    mockApi({ detailResponse: () => Promise.resolve(Response.json({ detail: 'Not found' }, { status: 404 })) })
    render(<App />)
    expect(await screen.findByText('词条不存在或无法访问')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回词库' })).toHaveAttribute('href', '/library')
    expect(screen.queryByText('琥珀；一种由古代树脂形成的黄色物质')).not.toBeInTheDocument()
  })

  it('loads the detail again after a direct URL refresh', async () => {
    window.history.replaceState({}, '', '/library/42')
    const requests = mockApi()
    const first = render(<App />)
    expect(await screen.findByText('The amber caught the morning light.')).toBeInTheDocument()
    first.unmount()
    render(<App />)
    expect(await screen.findByText('The amber caught the morning light.')).toBeInTheDocument()
    expect(requests.filter((url) => url === '/api/words/state/42')).toHaveLength(2)
  })

  it('returns to sign-in when the detail request loses authentication', async () => {
    window.history.replaceState({}, '', '/library/42')
    mockApi({ detailResponse: () => Promise.resolve(Response.json({ detail: '请先登录' }, { status: 401 })) })
    render(<App />)
    expect(await screen.findByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    expect(screen.queryByText('The amber caught the morning light.')).not.toBeInTheDocument()
  })

  it('keeps the return link and retries a failed detail request', async () => {
    window.history.replaceState({}, '', '/library/42')
    let failing = true
    mockApi({
      detailResponse: () => Promise.resolve(failing
        ? Response.json({ detail: '网络暂不可用' }, { status: 503 })
        : Response.json(detail)),
    })
    render(<App />)
    expect(await screen.findByText('网络暂不可用')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回词库' })).toBeInTheDocument()
    failing = false
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('The amber caught the morning light.')).toBeInTheDocument()
  })

  it('never shows the previous account detail after switching accounts', async () => {
    window.history.replaceState({}, '', '/library/42')
    let userId = 1
    mockApi({
      currentUser: () => userId,
      detailResponse: () => Promise.resolve(userId === 1
        ? Response.json(detail)
        : Response.json({ detail: 'Not found' }, { status: 404 })),
    })
    render(<App />)
    expect(await screen.findByText('The amber caught the morning light.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '退出登录' }))
    await screen.findByRole('form', { name: '登录拾词' })
    userId = 2
    await userEvent.type(screen.getByLabelText('用户名'), 'beta')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('词条不存在或无法访问')).toBeInTheDocument()
    expect(screen.queryByText('The amber caught the morning light.')).not.toBeInTheDocument()
  })

  it('does not request a malformed state id', async () => {
    window.history.replaceState({}, '', '/library/not-an-id')
    const requests = mockApi()
    render(<App />)
    expect(await screen.findByText('词条不存在或无法访问')).toBeInTheDocument()
    expect(requests.filter((url) => url.startsWith('/api/words/state/'))).toHaveLength(0)
  })

  it('returns through browser history with restored list state', async () => {
    mockApi()
    render(<App />)
    const input = await screen.findByRole('textbox', { name: '搜索单词' })
    await userEvent.type(input, 'amber')
    await userEvent.click(await screen.findByRole('link', { name: /amber/ }))
    expect(await screen.findByText('The amber caught the morning light.')).toBeInTheDocument()
    window.history.back()
    await waitFor(() => expect(window.location.pathname).toBe('/library'))
    expect(screen.getByRole('textbox', { name: '搜索单词' })).toHaveValue('amber')
  })
})
