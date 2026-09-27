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
  // The per-field source record the state route always carries. The word came from the
  // primary source at a pinned revision; the adopted meaning came from another source
  // that recorded no revision, and the primary's own meaning was recorded without
  // being adopted. No evidence row repeats a value asserted elsewhere in this file, so
  // an assertion that used to find one element still finds one.
  sources: {
    fields: [
      {
        field_kind: 'word',
        selected: [{
          source_evidence_id: 31, field_kind: 'word', row_locator: 2, sense_key: 'word@2',
          raw_text: 'amber', decision: 'selected', selected_for_default: true,
          selection_order: 0, source_revision: '6588944',
          source_revision_url: 'https://zh.wiktionary.org/w/index.php?oldid=6588944',
          source: {
            source_artifact_id: 3, role: 'primary', name: 'primary.csv',
            publisher: '考试词表发布方', version: '2026-09-25', license_id: 'CC-BY-SA-4.0',
          },
        }],
        candidates: [],
      },
      {
        field_kind: 'meaning',
        selected: [{
          source_evidence_id: 32, field_kind: 'meaning', row_locator: 2, sense_key: 'meaning@2',
          raw_text: '琥珀色的', decision: 'selected', selected_for_default: true,
          selection_order: 0, source_revision: '', source_revision_url: '',
          source: {
            source_artifact_id: 4, role: 'meaning', name: 'supplement.csv',
            publisher: '补充来源整理者', version: '2026-09-25', license_id: 'CC-BY-NC-SA-4.0',
          },
        }],
        candidates: [{
          source_evidence_id: 33, field_kind: 'meaning', row_locator: 2, sense_key: 'meaning@2',
          raw_text: '琥珀；树脂化石', decision: 'not_selected', selected_for_default: false,
          selection_order: null, source_revision: '1111111',
          source_revision_url: 'https://zh.wiktionary.org/w/index.php?oldid=1111111',
          source: {
            source_artifact_id: 3, role: 'primary', name: 'primary.csv',
            publisher: '考试词表发布方', version: '2026-09-25', license_id: 'CC-BY-SA-4.0',
          },
        }],
      },
    ],
    completeness: {
      status: 'incomplete',
      missing: [{
        code: 'source_revision_missing', field_kind: 'meaning',
        message: '字段「meaning」的采用来源没有记录固定修订号',
      }],
      message: '该词条的来源记录不完整：字段「meaning」的采用来源没有记录固定修订号。',
    },
  },
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
    // The right-hand panel is the other entry that reuses the detail component, so the
    // source record has to appear there too.
    expect(await screen.findByRole('region', { name: '来源记录' })).toBeInTheDocument()
  })

  it('shows the per-field sources through the reused detail entry', async () => {
    window.history.replaceState({}, '', '/library/42')
    mockApi()
    render(<App />)

    const record = await screen.findByRole('region', { name: '来源记录' })
    const wordRows = within(record).getByRole('list', { name: '单词：已采用的来源' })
    const meaningRows = within(record).getByRole('list', { name: '释义：已采用的来源' })
    const meaningCandidates = within(record).getByRole('list', { name: '释义：未采用的候选' })

    // The word came from the primary source, and its row carries the link to the exact
    // revision it was read at.
    expect(within(wordRows).getByText('考试词表发布方')).toBeInTheDocument()
    expect(within(wordRows).getByText('amber')).toBeInTheDocument()
    expect(within(wordRows).getByRole('link', { name: /固定修订/ })).toHaveAttribute(
      'href', 'https://zh.wiktionary.org/w/index.php?oldid=6588944',
    )
    // ...and the internal link to that source's card, in this word's own lexicon: the
    // anchor carries the lexicon id as well as the artifact's, so a source file shared
    // with another lexicon cannot send the reader into that lexicon's section.
    expect(within(wordRows).getByRole('link', { name: '来源详情' })).toHaveAttribute(
      'href', '/sources#source-1-3',
    )

    // The meaning came from a different source: its publisher must not appear as the
    // origin of the word, nor the word's as the origin of the meaning.
    expect(within(meaningRows).getByText('补充来源整理者')).toBeInTheDocument()
    expect(within(meaningRows).getByRole('link', { name: '来源详情' })).toHaveAttribute(
      'href', '/sources#source-1-4',
    )
    expect(within(wordRows).queryByText('补充来源整理者')).not.toBeInTheDocument()

    // The primary's own meaning was recorded and not adopted, so it is listed as a
    // candidate and nowhere near the adopted value.
    expect(within(meaningCandidates).getByText('琥珀；树脂化石')).toBeInTheDocument()
    expect(within(meaningCandidates).getByText('未采用')).toBeInTheDocument()
    expect(within(meaningRows).queryByText('琥珀；树脂化石')).not.toBeInTheDocument()

    // The recorded revision is missing for that row, so no link is built for it and the
    // state is said out loud instead.
    expect(within(meaningRows).getByText(/没有记录固定修订号/)).toBeInTheDocument()
    expect(within(meaningRows).queryByRole('link', { name: /固定修订/ })).not.toBeInTheDocument()

    // A partial record says so, and the declaration is quoted as a declaration.
    expect(within(record).getByText(/该词条的来源记录不完整/)).toBeInTheDocument()
    expect(within(record).getByText('以上是导入时记录的来源声明，不代表授权已获确认。')).toBeInTheDocument()

    // The source's own line and the full meanings are still on the page.
    expect(screen.getByText('琥珀；一种由古代树脂形成的黄色物质')).toBeInTheDocument()
    expect(screen.getByText('查看原书原文')).toBeInTheDocument()
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
