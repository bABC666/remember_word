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
  // No confirmed short meaning: the detail page must still show the source exactly
  // as it did before this slice, so the fallback is exercised by every test here.
  concise_meanings: [],
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
  listResponse?: () => Promise<Response>
} = {}) {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    requests.push(url)
    if (url === '/api/auth/me') return Promise.resolve(Response.json(me(options.currentUser?.() ?? 1)))
    if (url === '/api/auth/logout') return Promise.resolve(Response.json({ ok: true }))
    if (url === '/api/auth/login') return Promise.resolve(Response.json(me(options.currentUser?.() ?? 1)))
    if (url === '/api/settings/onboarding') return Promise.resolve(Response.json({ seen: true }))
    if (url === '/api/dashboard') return Promise.resolve(Response.json({ today_new: 0, due_reviews: 0, weak_words: 0, reading_status: 'not_generated', streak_days: 0 }))
    if (url.startsWith('/api/words?')) {
      if (options.listResponse) return options.listResponse()
      return Promise.resolve(Response.json({ total: 1, words: [savedWord] }))
    }
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
  vi.stubGlobal('scrollTo', vi.fn())
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

  it('returns to the original filtered list after visiting another page and browser back', async () => {
    mockApi()
    render(<App />)
    await userEvent.type(await screen.findByRole('textbox', { name: '搜索单词' }), 'amber')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'weak')
    await userEvent.click(screen.getByRole('button', { name: '最近加入' }))
    document.querySelector<HTMLElement>('.word-list')!.scrollTop = 135
    await userEvent.click(await screen.findByRole('link', { name: /amber/ }))
    await screen.findByText('The amber caught the morning light.')
    await userEvent.click(screen.getByRole('link', { name: '今日概览' }))
    expect(await screen.findByRole('heading', { name: '今天也从一个词开始。' })).toBeInTheDocument()
    window.history.back()
    await waitFor(() => expect(window.location.pathname).toBe('/library/42'))
    await screen.findByText('The amber caught the morning light.')
    const historyGo = vi.spyOn(window.history, 'go')
    await userEvent.click(screen.getByRole('link', { name: '返回词库' }))
    expect(historyGo).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe('/library')
    expect(new URLSearchParams(window.location.search).get('search')).toBe('amber')
    expect(new URLSearchParams(window.location.search).get('status')).toBe('weak')
    expect(new URLSearchParams(window.location.search).get('view')).toBe('recent')
    await waitFor(() => {
      expect(document.querySelector<HTMLElement>('.word-list')!.scrollTop).toBe(135)
      expect(screen.getByRole('link', { name: /amber/ })).toHaveFocus()
    })
  })

  it('keeps the filtered return URL after returning to detail through browser history', async () => {
    mockApi()
    render(<App />)
    await userEvent.type(await screen.findByRole('textbox', { name: '搜索单词' }), 'amber')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'weak')
    await userEvent.click(screen.getByRole('button', { name: '最近加入' }))
    document.querySelector<HTMLElement>('.word-list')!.scrollTop = 135
    await userEvent.click(await screen.findByRole('link', { name: /amber/ }))
    await screen.findByText('The amber caught the morning light.')
    await userEvent.click(screen.getByRole('link', { name: '返回词库' }))
    await waitFor(() => expect(window.location.pathname).toBe('/library'))
    window.history.back()
    await waitFor(() => expect(window.location.pathname).toBe('/library/42'))
    await screen.findByText('The amber caught the morning light.')
    const returnLink = screen.getByRole('link', { name: '返回词库' })
    expect(returnLink).toHaveAttribute('href', '/library?search=amber&status=weak&view=recent')
    await userEvent.click(returnLink)
    expect(window.location.pathname).toBe('/library')
    expect(new URLSearchParams(window.location.search).get('search')).toBe('amber')
    expect(new URLSearchParams(window.location.search).get('status')).toBe('weak')
    expect(new URLSearchParams(window.location.search).get('view')).toBe('recent')
    await waitFor(() => {
      expect(document.querySelector<HTMLElement>('.word-list')!.scrollTop).toBe(135)
      expect(screen.getByRole('link', { name: /amber/ })).toHaveFocus()
    })
  })

  it('shows first and latest article exposure times and omits absent dates', async () => {
    mockApi()
    window.history.replaceState({}, '', '/library/42')
    render(<App />)
    const exposure = await screen.findByRole('region', { name: '文章暴露' })
    expect(within(exposure).getByText(/首次.*2026\/9\/21/)).toBeInTheDocument()
    expect(within(exposure).getByText(/最近.*2026\/9\/23/)).toBeInTheDocument()
  })

  it('does not render invalid article exposure dates when timestamps are null', async () => {
    window.history.replaceState({}, '', '/library/42')
    mockApi({ detailResponse: () => Promise.resolve(Response.json({
      ...detail,
      article_exposures: [{ ...detail.article_exposures[0], first_exposed_at: null, last_exposed_at: null }],
    })) })
    render(<App />)
    const exposure = await screen.findByRole('region', { name: '文章暴露' })
    expect(within(exposure).getByText('The amber caught the morning light.')).toBeInTheDocument()
    expect(within(exposure).queryByText(/首次|最近|Invalid Date/)).not.toBeInTheDocument()
  })

  it('shows the available article exposure date when the first time is null', async () => {
    window.history.replaceState({}, '', '/library/42')
    mockApi({ detailResponse: () => Promise.resolve(Response.json({
      ...detail,
      article_exposures: [{ ...detail.article_exposures[0], first_exposed_at: null }],
    })) })
    render(<App />)
    const exposure = await screen.findByRole('region', { name: '文章暴露' })
    expect(within(exposure).queryByText(/首次/)).not.toBeInTheDocument()
    expect(within(exposure).getByText(/最近.*2026\/9\/23/)).toBeInTheDocument()
  })

  it('clears a filtered library URL and position before another account signs in', async () => {
    vi.stubGlobal('scrollY', 240)
    vi.stubGlobal('scrollTo', vi.fn((_x: number, y: number) => vi.stubGlobal('scrollY', y)))
    let userId = 1
    const requests = mockApi({ currentUser: () => userId })
    render(<App />)
    await userEvent.type(await screen.findByRole('textbox', { name: '搜索单词' }), 'amber')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'weak')
    await userEvent.click(screen.getByRole('button', { name: '最近加入' }))
    document.querySelector<HTMLElement>('.word-list')!.scrollTop = 135
    await userEvent.click(screen.getByRole('button', { name: '退出登录' }))
    await screen.findByRole('form', { name: '登录拾词' })
    userId = 2
    await userEvent.type(screen.getByLabelText('用户名'), 'beta')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    const search = await screen.findByRole('textbox', { name: '搜索单词' })
    await screen.findByRole('link', { name: /amber/ })
    expect(window.location.pathname).toBe('/library')
    expect(window.location.search).toBe('')
    expect(search).toHaveValue('')
    expect(screen.getByRole('combobox', { name: '学习状态' })).toHaveValue('')
    expect(screen.getByRole('button', { name: '最近加入' })).toHaveAttribute('aria-pressed', 'false')
    expect(document.querySelector<HTMLElement>('.word-list')!.scrollTop).toBe(0)
    expect(window.scrollY).toBe(0)
    expect(requests.filter((url) => url.startsWith('/api/words?')).at(-1))
      .toBe('/api/words?search=&status=&view=')
  })

  it('clears A filters and position after a library 401, including browser back under B', async () => {
    let userId = 1
    let expireA = false
    vi.stubGlobal('scrollY', 240)
    vi.stubGlobal('scrollTo', vi.fn((_x: number, y: number) => vi.stubGlobal('scrollY', y)))
    const requests = mockApi({
      currentUser: () => userId,
      listResponse: () => Promise.resolve(expireA && userId === 1
        ? Response.json({ detail: '请先登录' }, { status: 401 })
        : Response.json({ total: 1, words: [{ ...savedWord, word: userId === 1 ? 'amber' : 'beta' }] })),
    })
    render(<App />)
    await userEvent.type(await screen.findByRole('textbox', { name: '搜索单词' }), 'amber')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'weak')
    await userEvent.click(screen.getByRole('button', { name: '最近加入' }))
    document.querySelector<HTMLElement>('.word-list')!.scrollTop = 135
    screen.getByRole('link', { name: /amber/ }).focus()
    await userEvent.click(screen.getByRole('link', { name: '今日概览' }))
    await screen.findByRole('heading', { name: '今天也从一个词开始。' })
    await userEvent.click(screen.getByRole('link', { name: '我的词库' }))
    await screen.findByRole('textbox', { name: '搜索单词' })
    await userEvent.type(screen.getByRole('textbox', { name: '搜索单词' }), 'amber')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'weak')
    expireA = true
    await userEvent.click(screen.getByRole('button', { name: '长期未复习' }))
    await screen.findByRole('form', { name: '登录拾词' })
    userId = 2
    await userEvent.type(screen.getByLabelText('用户名'), 'beta')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByRole('textbox', { name: '搜索单词' })).toHaveValue('')
    await screen.findByRole('link', { name: /beta/ })
    expect(window.location.pathname + window.location.search).toBe('/library')
    expect(document.querySelector<HTMLElement>('.word-list')!.scrollTop).toBe(0)
    expect(window.scrollY).toBe(0)
    expect(screen.getByRole('link', { name: /beta/ })).not.toHaveFocus()
    window.history.back()
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    window.history.back()
    await waitFor(() => {
      expect(window.location.pathname + window.location.search).toBe('/library')
      expect(screen.getByRole('textbox', { name: '搜索单词' })).toHaveValue('')
      expect(screen.getByRole('combobox', { name: '学习状态' })).toHaveValue('')
      expect(screen.getByRole('button', { name: '最近加入' })).toHaveAttribute('aria-pressed', 'false')
      expect(document.querySelector<HTMLElement>('.word-list')!.scrollTop).toBe(0)
      expect(window.scrollY).toBe(0)
      expect(screen.getByRole('link', { name: /beta/ })).not.toHaveFocus()
    })
    expect(requests.filter((url) => url.startsWith('/api/words?')).at(-1))
      .toBe('/api/words?search=&status=&view=')
  })

  it.each(['/library/', '/Library'])('clears A filters after a 401 at %s', async (path) => {
    window.history.replaceState({}, '', `${path}?search=amber&status=weak`)
    let userId = 1
    let expireA = false
    const requests = mockApi({
      currentUser: () => userId,
      listResponse: () => Promise.resolve(expireA && userId === 1
        ? Response.json({ detail: '请先登录' }, { status: 401 })
        : Response.json({ total: 1, words: [{ ...savedWord, word: userId === 1 ? 'amber' : 'beta' }] })),
    })
    render(<App />)
    expect(await screen.findByRole('textbox', { name: '搜索单词' })).toHaveValue('amber')
    expireA = true
    await userEvent.click(screen.getByRole('button', { name: '最近加入' }))
    await screen.findByRole('form', { name: '登录拾词' })
    userId = 2
    await userEvent.type(screen.getByLabelText('用户名'), 'beta')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByRole('textbox', { name: '搜索单词' })).toHaveValue('')
    expect(window.location.pathname + window.location.search).toBe('/library')
    expect(requests.filter((url) => url.startsWith('/api/words?')).at(-1))
      .toBe('/api/words?search=&status=&view=')
  })

  it('keeps a filtered library URL through a normal refresh of the same account', async () => {
    mockApi()
    const first = render(<App />)
    await userEvent.type(await screen.findByRole('textbox', { name: '搜索单词' }), 'amber')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'weak')
    await userEvent.click(screen.getByRole('button', { name: '最近加入' }))
    first.unmount()
    render(<App />)
    expect(await screen.findByRole('textbox', { name: '搜索单词' })).toHaveValue('amber')
    expect(screen.getByRole('combobox', { name: '学习状态' })).toHaveValue('weak')
    expect(screen.getByRole('button', { name: '最近加入' })).toHaveAttribute('aria-pressed', 'true')
  })

  it.each([0, 240])('moves to detail top and restores document scroll %i on return', async (initialY) => {
    let pageY = initialY
    vi.stubGlobal('scrollY', pageY)
    const scrollTo = vi.fn((_x: number, y: number) => {
      pageY = y
      vi.stubGlobal('scrollY', pageY)
    })
    vi.stubGlobal('scrollTo', scrollTo)
    mockApi({ detailResponse: () => Promise.resolve(Response.json({
      ...detail,
      source_meanings: Array.from({ length: 40 }, (_, index) => `长释义 ${index + 1}`),
    })) })
    render(<App />)
    const wordLink = await screen.findByRole('link', { name: /amber/ })
    document.querySelector<HTMLElement>('.word-list')!.scrollTop = 135
    await userEvent.click(wordLink)
    expect(await screen.findByText('长释义 40')).toBeInTheDocument()
    expect(window.scrollY).toBe(0)
    expect(scrollTo).toHaveBeenCalledWith(0, 0)
    vi.stubGlobal('scrollY', 500)
    await userEvent.click(screen.getByRole('link', { name: '返回词库' }))
    await waitFor(() => {
      expect(window.scrollY).toBe(initialY)
      expect(document.querySelector<HTMLElement>('.word-list')!.scrollTop).toBe(135)
      expect(screen.getByRole('link', { name: /amber/ })).toHaveFocus()
    })
    expect(scrollTo).toHaveBeenLastCalledWith(0, initialY)
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
