import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

const json = (value: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }))

const unauthorized = () => json({ detail: '请先登录' }, 401)

const ADMIN = {
  id: 1, username: 'admin', display_name: 'admin', role: 'admin', is_admin: true,
  settings: { daily_new_words: 15, article_length: 650, onboarding_seen: true },
}
const USER_B = {
  id: 2, username: 'userb', display_name: 'userb', role: 'user', is_admin: false,
  settings: { daily_new_words: 5, article_length: 300, onboarding_seen: true },
}

const ADMIN_DASHBOARD = { today_new: 9, due_reviews: 7, weak_words: 3, reading_status: 'completed', streak_days: 42 }
const USER_B_DASHBOARD = { today_new: 0, due_reviews: 0, weak_words: 0, reading_status: 'not_generated', streak_days: 0 }

const ADMIN_ARTICLE = {
  id: 11, title: 'ADMIN PRIVATE ARTICLE', content: 'ADMIN PRIVATE CONTENT', created_at: '2026-09-22T00:00:00Z',
  target_words: [], completed: true, translation: 'ADMIN TRANSLATION',
}

/** Records the order of requests so retry behaviour can be asserted. */
let calls: string[] = []

function route(url: string, method: string, who: 'none' | 'admin' | 'userb') {
  const signingIn = url.includes('/api/auth/login')
  if (signingIn) return who === 'admin' ? json(ADMIN) : json(USER_B)
  if (url.includes('/api/auth/me')) return who === 'none' ? unauthorized() : json(who === 'admin' ? ADMIN : USER_B)
  if (url.includes('/api/auth/logout')) return json({ ok: true })
  if (url.includes('/api/settings/onboarding')) return json({ seen: true })
  if (url.endsWith('/api/settings')) return json({ ...ADMIN, can_manage_instance_settings: who === 'admin' })
  if (url.endsWith('/api/dashboard')) {
    return json(who === 'admin' ? ADMIN_DASHBOARD : USER_B_DASHBOARD)
  }
  if (url.endsWith('/api/articles')) return json(who === 'admin' ? [ADMIN_ARTICLE] : [])
  if (url.endsWith('/api/imports')) return json([])
  if (url.includes('/api/words')) {
    return json(
      who === 'admin'
        ? { total: 1, words: [{ id: 1, word_state_id: 3, legacy_word_id: 1, lexicon_entry_id: 2, lexicon_id: 1, word: 'ADMINWORD', status: 'weak', phonetic: '', part_of_speech: '', source_meanings: ['x'], source_raw: 'x', anchor: 'x', semantic_note: '', first_seen: '', last_review: null, next_review_at: null, recall_success: 0, recall_fail: 0, context_exposure: 0, possible_issue: false, notes: '' }] }
        : { total: 0, words: [] },
    )
  }
  void method
  return json({ total: 0, words: [] })
}

let current: 'none' | 'admin' | 'userb' = 'none'

beforeEach(() => {
  calls = []
  current = 'none'
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push(`${method} ${url}`)

      if (url.includes('/api/auth/login')) {
        const body = JSON.parse(String(init?.body ?? '{}')) as { username?: string }
        current = body.username === 'admin' ? 'admin' : 'userb'
        return route(url, method, current)
      }
      if (url.includes('/api/auth/logout')) {
        current = 'none'
        return route(url, method, 'none')
      }
      return route(url, method, current)
    }),
  )
})

describe('sign-in gate', () => {
  it('shows only the login screen when there is no session', async () => {
    render(<App />)
    expect(await screen.findByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    // No private page is rendered behind the gate.
    expect(screen.queryByText('今天也从一个词开始。')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '我的词库' })).not.toBeInTheDocument()
  })

  it('signs in and then shows the application', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })

    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByText('今天也从一个词开始。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '我的词库' })).toBeInTheDocument()
  })

  it('shows a loading state while signing in', async () => {
    let release: (() => void) | undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const original = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes('/api/auth/login')) {
        await gate
      }
      return original(input, init)
    })

    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('button', { name: '正在登录…' })).toBeDisabled()
    release?.()
    await screen.findByText('今天也从一个词开始。')
  })

  it('reports a wrong password without leaving the login screen', async () => {
    const original = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes('/api/auth/login')) return json({ detail: '用户名或密码不正确' }, 401)
      return original(input, init)
    })

    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码不正确')
    expect(screen.getByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    expect(screen.queryByText('今天也从一个词开始。')).not.toBeInTheDocument()
  })
})

describe('expired session', () => {
  it('returns to the login screen and leaves no cached private data behind', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    await screen.findByRole('link', { name: '我的词库' })

    // Load private data so the cache is definitely populated.
    await userEvent.click(screen.getByRole('link', { name: '我的词库' }))
    expect(await screen.findByText('ADMINWORD')).toBeInTheDocument()

    // The session ends. Whatever ended it, the client must drop the session and
    // every cached response with it.
    current = 'none'
    await userEvent.click(screen.getByRole('button', { name: '退出登录' }))

    expect(await screen.findByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    expect(screen.queryByText('ADMINWORD')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '我的词库' })).not.toBeInTheDocument()
  })

  it('treats a 401 from any endpoint as the end of the session', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    await screen.findByRole('link', { name: '我的词库' })

    // Read one page first, then expire the session and open a page whose data was
    // never fetched, so a real request goes out and comes back 401.
    await userEvent.click(screen.getByRole('link', { name: '我的词库' }))
    await screen.findByText('ADMINWORD')

    // Every subsequent request is rejected as unauthenticated.
    const original = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes('/api/auth/logout')) return original(input, init)
      return unauthorized()
    })
    current = 'none'

    await userEvent.click(screen.getByRole('link', { name: '设置' }))
    expect(await screen.findByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    expect(screen.queryByText('ADMINWORD')).not.toBeInTheDocument()
  })

  it('does not retry an authentication failure', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    const meCalls = calls.filter((entry) => entry.includes('/api/auth/me'))
    expect(meCalls.length).toBeLessThanOrEqual(2)
  })
})

describe('account switching', () => {
  it('shows nothing of the previous user after logging out and signing in as another', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })

    // --- admin signs in and loads their data -----------------------------
    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    await screen.findByRole('link', { name: '我的词库' })

    await userEvent.click(screen.getByRole('link', { name: '我的词库' }))
    expect(await screen.findByText('ADMINWORD')).toBeInTheDocument()

    // --- admin logs out ---------------------------------------------------
    await userEvent.click(screen.getByRole('button', { name: '退出登录' }))
    await screen.findByRole('form', { name: '登录拾词' })

    // --- a different user signs in ---------------------------------------
    current = 'userb'
    await userEvent.type(screen.getByLabelText('用户名'), 'userb')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))

    // The new user's own shell is shown, identified by the account block.
    expect(await screen.findByText('userb')).toBeInTheDocument()
    // ...and nothing of the previous account survives, even before the new
    // requests have completed.
    expect(screen.queryByText('ADMINWORD')).not.toBeInTheDocument()
    expect(screen.queryByText('42')).not.toBeInTheDocument()
    expect(screen.queryByText('ADMIN PRIVATE ARTICLE')).not.toBeInTheDocument()

    // Visit the same page the previous account had loaded. Its cached data must
    // be gone, so this must show the new account's empty library rather than the
    // previous account's word.
    await userEvent.click(screen.getByRole('link', { name: '我的词库' }))
    await waitFor(() => expect(screen.queryByText('ADMINWORD')).not.toBeInTheDocument())
  })

  it('shows the new account name immediately after switching', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('admin')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '退出登录' }))
    await screen.findByRole('form', { name: '登录拾词' })

    current = 'userb'
    await userEvent.type(screen.getByLabelText('用户名'), 'userb')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByText('userb')).toBeInTheDocument()
    expect(screen.queryByText('管理员')).not.toBeInTheDocument()
  })
})

describe('admin-only UI', () => {
  it('hides the admin marker from a normal user', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    await userEvent.type(screen.getByLabelText('用户名'), 'userb')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    await screen.findByText('userb')
    expect(screen.queryByText('管理员')).not.toBeInTheDocument()
  })

  it('never stores the session token or user id in localStorage', async () => {
    render(<App />)
    await screen.findByRole('form', { name: '登录拾词' })
    await userEvent.type(screen.getByLabelText('用户名'), 'admin')
    await userEvent.type(screen.getByLabelText('密码'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '登录' }))
    await screen.findByRole('link', { name: '我的词库' })

    // Nothing about the session is persisted client-side: the token lives in an
    // HttpOnly cookie the browser manages and JavaScript cannot read.
    const storage = window.localStorage
    if (storage) {
      expect(Object.keys(storage)).toHaveLength(0)
      expect(storage.getItem('shici_session')).toBeNull()
    } else {
      // No web storage at all is the strongest possible form of "nothing kept".
      expect(storage).toBeUndefined()
    }
  })
})
