import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

/**
 * F-1 (signed-in devices) and F-7 (change your own password).
 *
 * Three properties are asserted beyond the happy path, because they are the ones a
 * mistake would quietly break:
 *
 * * no token and no ``token_hash`` ever reaches the DOM -- the fixture deliberately
 *   contains both, so a leak would show up as text;
 * * the device the caller is using right now offers no revoke button, so it cannot
 *   be signed out by accident;
 * * both revocations and a password change go through the password prompt, and a
 *   refused or rate-limited attempt changes nothing on screen.
 *
 * One assertion here is deliberately jsdom-only: the component's too-short branch is
 * pre-empted in a real browser by the field's native `minLength` validation, so it
 * guards the branch rather than the message a user actually sees (see the DoD 4
 * browser acceptance, §3).
 */

const json = (value: unknown, status = 200, headers: Record<string, string> = {}) =>
  Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { 'Content-Type': 'application/json', ...headers },
    }),
  )

const ADMIN = {
  id: 1, username: 'admin', display_name: 'admin', role: 'admin', is_admin: true,
  settings: { daily_new_words: 15, article_length: 650, onboarding_seen: true },
}

const SETTINGS = {
  deepseek_api_key_configured: true, deepseek_api_key_masked: 'sk-…abcd', deepseek_base_url: 'https://api.deepseek.com',
  deepseek_model: 'deepseek-flash', deepseek_model_display: 'DeepSeek V4.1-Flash', daily_new_words: 15, article_length: 650,
  ocr_language: 'en', ocr_use_gpu: false, paddleocr_available: true, paddleocr_message: 'PaddleOCR 可用',
  data_directory: 'D:\\data', database_path: 'D:\\data\\vocab.db', backups_directory: 'D:\\data\\backups',
  can_manage_instance_settings: true, onboarding_seen: true,
}

const DASHBOARD = { today_new: 9, due_reviews: 7, weak_words: 3, reading_status: 'completed', streak_days: 42 }

/** What a leak would look like: fields the server must never send, in the fixture. */
const TOKEN_HASH = 'f'.repeat(64)
const RAW_TOKEN = 'raw-session-token-value'

const CURRENT_DEVICE = 'Mozilla/5.0 (Windows NT 10.0) CurrentBrowser'
const OTHER_DEVICE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) OtherPhone'

let requests: { method: string; url: string; body: string }[] = []
let revokedIds: number[] = []
/** The gate asks `/api/auth/me` first, so a session has to be earned by signing in. */
let signedIn = false
/** Per-test response overrides, so a refusal can be simulated. */
let oneResponse: (() => Promise<Response>) | null = null
let othersResponse: (() => Promise<Response>) | null = null
let passwordResponse: (() => Promise<Response>) | null = null

function sessionRows() {
  return [
    {
      id: 7, current: true, created_at: '2026-09-23T01:00:00Z', last_seen_at: null,
      expires_at: '2026-10-23T01:00:00Z', user_agent: CURRENT_DEVICE,
      token_hash: TOKEN_HASH, token: RAW_TOKEN,
    },
    {
      id: 8, current: false, created_at: '2026-09-22T10:00:00Z', last_seen_at: '2026-09-22T11:30:00Z',
      expires_at: '2026-10-22T10:00:00Z', user_agent: OTHER_DEVICE,
      token_hash: TOKEN_HASH, token: RAW_TOKEN,
    },
  ].filter((row) => !revokedIds.includes(row.id))
}

function route(url: string, method: string): Promise<Response> {
  if (url.includes('/api/auth/logout')) {
    signedIn = false
    return json({ ok: true })
  }
  if (url.includes('/api/auth/login')) {
    signedIn = true
    return json(ADMIN)
  }
  if (url.includes('/api/auth/me')) return signedIn ? json(ADMIN) : json({ detail: '请先登录' }, 401)
  if (url.includes('/api/auth/sessions/revoke')) {
    if (othersResponse) return othersResponse()
    const revoked = sessionRows().filter((row) => !row.current).length
    revokedIds = revokedIds.concat(sessionRows().filter((row) => !row.current).map((row) => row.id))
    return json({ ok: true, revoked })
  }
  if (url.includes('/api/auth/sessions/')) {
    if (oneResponse) return oneResponse()
    const id = Number(url.split('/').pop())
    revokedIds = revokedIds.concat([id])
    return json({ ok: true })
  }
  if (url.endsWith('/api/auth/sessions')) return json({ sessions: sessionRows() })
  if (url.includes('/api/auth/password')) {
    if (passwordResponse) return passwordResponse()
    return json({ ok: true })
  }
  if (url.includes('/api/settings/onboarding')) return json({ seen: true })
  if (url.endsWith('/api/settings')) return json(SETTINGS)
  if (url.endsWith('/api/dashboard')) return json(DASHBOARD)
  if (url.endsWith('/api/articles')) return json([])
  if (url.endsWith('/api/imports')) return json([])
  void method
  return json({ total: 0, words: [] })
}

beforeEach(() => {
  requests = []
  revokedIds = []
  signedIn = false
  oneResponse = null
  othersResponse = null
  passwordResponse = null
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ method, url, body: String(init?.body ?? '') })
      return route(url, method)
    }),
  )
})

/** Sign in and open the settings page, which is where both features live. */
async function openSettings() {
  render(<App />)
  await screen.findByRole('form', { name: '登录拾词' })
  await userEvent.type(screen.getByLabelText('用户名'), 'admin')
  await userEvent.type(screen.getByLabelText('密码'), 'secret')
  await userEvent.click(screen.getByRole('button', { name: '登录' }))
  await screen.findByRole('link', { name: '设置' })
  await userEvent.click(screen.getByRole('link', { name: '设置' }))
  await screen.findByText('登录设备')
}

describe('signed-in devices', () => {
  it('lists device metadata and never renders a token', async () => {
    await openSettings()

    expect(await screen.findByText(CURRENT_DEVICE)).toBeInTheDocument()
    expect(screen.getByText(OTHER_DEVICE)).toBeInTheDocument()
    // The metadata the endpoint is contracted to return.
    expect(screen.getByText('当前设备')).toBeInTheDocument()
    expect(screen.getAllByText('最近活动').length).toBe(2)
    expect(screen.getAllByText('登录时间').length).toBe(2)
    expect(screen.getAllByText('到期时间').length).toBe(2)
    // A never-used session says so rather than showing "Invalid Date".
    expect(screen.getByText('—')).toBeInTheDocument()

    // G8: the user-agent line is ellipsized so one long string cannot widen the
    // page, which only stays acceptable because the full value is still there.
    expect(screen.getByText(CURRENT_DEVICE)).toHaveAttribute('title', CURRENT_DEVICE)
    expect(screen.getByText(OTHER_DEVICE)).toHaveAttribute('title', OTHER_DEVICE)

    // Neither the response's extra fields nor any 64-hex string may reach the DOM.
    const text = document.body.textContent ?? ''
    expect(text).not.toContain(TOKEN_HASH)
    expect(text).not.toContain(RAW_TOKEN)
    expect(text).not.toMatch(/[0-9a-f]{64}/)
  })

  it('offers no way to revoke the device the caller is using', async () => {
    await openSettings()
    await screen.findByText(CURRENT_DEVICE)

    // Exactly one revoke button: the other device's. The current one explains why.
    expect(screen.getAllByRole('button', { name: '撤销该设备' })).toHaveLength(1)
    expect(screen.getByText(/当前设备不能在这里撤销/)).toBeInTheDocument()
  })

  it('asks for the password before revoking one device, then revokes it', async () => {
    await openSettings()
    await screen.findByText(OTHER_DEVICE)

    await userEvent.click(screen.getByRole('button', { name: '撤销该设备' }))
    const dialog = await screen.findByRole('dialog', { name: '撤销这台设备' })
    // Nothing is sent until a password is typed.
    expect(within(dialog).getByRole('button', { name: '确认撤销' })).toBeDisabled()

    await userEvent.type(within(dialog).getByLabelText('当前密码'), 'secret')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认撤销' }))

    await waitFor(() => {
      const sent = requests.filter((entry) => entry.method === 'DELETE')
      expect(sent).toHaveLength(1)
      expect(sent[0].url).toBe('/api/auth/sessions/8')
      expect(JSON.parse(sent[0].body)).toEqual({ current_password: 'secret' })
    })
    expect(await screen.findByText('已撤销该设备，它需要重新登录。')).toBeInTheDocument()
    // The list is refetched, so the revoked device is gone.
    await waitFor(() => expect(screen.queryByText(OTHER_DEVICE)).not.toBeInTheDocument())
    expect(screen.getByText(CURRENT_DEVICE)).toBeInTheDocument()
  })

  it('revokes nothing when the password is refused', async () => {
    oneResponse = () => json({ detail: '当前密码不正确' }, 400)
    await openSettings()
    await screen.findByText(OTHER_DEVICE)

    await userEvent.click(screen.getByRole('button', { name: '撤销该设备' }))
    const dialog = await screen.findByRole('dialog', { name: '撤销这台设备' })
    await userEvent.type(within(dialog).getByLabelText('当前密码'), 'wrong')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认撤销' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('当前密码不正确')
    // The device is still signed in, and no success message appeared.
    expect(screen.getByText(OTHER_DEVICE)).toBeInTheDocument()
    expect(screen.queryByText('已撤销该设备，它需要重新登录。')).not.toBeInTheDocument()
  })

  it('shows how long to wait when the re-auth budget is exhausted', async () => {
    oneResponse = () => json({ detail: '密码校验尝试过于频繁，请稍后再试' }, 429, { 'Retry-After': '42' })
    await openSettings()
    await screen.findByText(OTHER_DEVICE)

    await userEvent.click(screen.getByRole('button', { name: '撤销该设备' }))
    const dialog = await screen.findByRole('dialog', { name: '撤销这台设备' })
    await userEvent.type(within(dialog).getByLabelText('当前密码'), 'secret')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认撤销' }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('密码校验尝试过于频繁')
    expect(alert).toHaveTextContent('42 秒后可重试')
    // While the wait lasts the button is disabled, so retrying is not a trap.
    expect(within(dialog).getByRole('button', { name: '确认撤销' })).toBeDisabled()
  })

  it('signs out every other device after the password is confirmed', async () => {
    await openSettings()
    await screen.findByText(OTHER_DEVICE)

    await userEvent.click(screen.getByRole('button', { name: '退出其它所有设备' }))
    const dialog = await screen.findByRole('dialog', { name: '退出其它所有设备' })
    await userEvent.type(within(dialog).getByLabelText('当前密码'), 'secret')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认退出' }))

    await waitFor(() => {
      const sent = requests.filter((entry) => entry.url === '/api/auth/sessions/revoke')
      expect(sent).toHaveLength(1)
      expect(JSON.parse(sent[0].body)).toEqual({ scope: 'others', current_password: 'secret' })
    })
    expect(await screen.findByText('已退出 1 台其它设备。')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(OTHER_DEVICE)).not.toBeInTheDocument())
    // The device making the request is untouched: it never asked for scope "all".
    expect(screen.getByText(CURRENT_DEVICE)).toBeInTheDocument()
  })
})

describe('changing your own password', () => {
  async function fill(form: HTMLElement, current: string, next: string, confirm: string) {
    await userEvent.type(within(form).getByLabelText('当前密码'), current)
    await userEvent.type(within(form).getByLabelText('新密码'), next)
    await userEvent.type(within(form).getByLabelText('确认新密码'), confirm)
  }

  it('changes it, then returns to the sign-in screen with an explanation', async () => {
    await openSettings()
    const form = await screen.findByRole('form', { name: '修改密码' })
    await fill(form, 'secret', 'brand-new-pass', 'brand-new-pass')
    await userEvent.click(within(form).getByRole('button', { name: '修改密码' }))

    await waitFor(() => {
      const sent = requests.filter((entry) => entry.url === '/api/auth/password')
      expect(sent).toHaveLength(1)
      expect(sent[0].method).toBe('POST')
      expect(JSON.parse(sent[0].body)).toEqual({
        current_password: 'secret',
        new_password: 'brand-new-pass',
      })
    })

    // Every session was revoked server-side, so the app must not pretend otherwise.
    expect(await screen.findByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    expect(screen.getByText('密码已更新，请用新密码重新登录。')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '我的词库' })).not.toBeInTheDocument()
  })

  it('refuses a mismatched confirmation without calling the server', async () => {
    await openSettings()
    const form = await screen.findByRole('form', { name: '修改密码' })
    await fill(form, 'secret', 'brand-new-pass', 'brand-new-pas')
    await userEvent.click(within(form).getByRole('button', { name: '修改密码' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent('两次输入的新密码不一致')
    expect(requests.filter((entry) => entry.url === '/api/auth/password')).toHaveLength(0)
    // Still signed in: nothing happened at all.
    expect(screen.getByRole('link', { name: '设置' })).toBeInTheDocument()
  })

  /**
   * The component's own too-short branch -- and a jsdom-only one.
   *
   * A real browser never reaches this branch: the 新密码 field carries minLength={8},
   * so Chrome refuses the submit itself and the user sees the browser's own message
   * ("请将该文本增加为 8 个字符或更多…") instead of 新密码至少 8 位。. That is the
   * behaviour the DoD 4 browser acceptance recorded as step S10
   * (docs/V1.2-PHASE2.8-F-DOD4-BROWSER-ACCEPTANCE.md §3), and the native check is kept
   * deliberately. jsdom implements no native constraint validation, so this test pins
   * the branch as a regression guard -- it does not describe what a user sees in a
   * browser, and the assertion below is not weakened to pretend otherwise.
   */
  it('refuses a too-short new password in the component, without calling the server', async () => {
    await openSettings()
    const form = await screen.findByRole('form', { name: '修改密码' })
    await fill(form, 'secret', 'short', 'short')
    await userEvent.click(within(form).getByRole('button', { name: '修改密码' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent('新密码至少 8 位')
    expect(requests.filter((entry) => entry.url === '/api/auth/password')).toHaveLength(0)
  })

  it('reports a refused current password and stays on the page', async () => {
    passwordResponse = () => json({ detail: '当前密码不正确' }, 400)
    await openSettings()
    const form = await screen.findByRole('form', { name: '修改密码' })
    await fill(form, 'wrong', 'brand-new-pass', 'brand-new-pass')
    await userEvent.click(within(form).getByRole('button', { name: '修改密码' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent('当前密码不正确')
    expect(screen.getByRole('link', { name: '设置' })).toBeInTheDocument()
  })
})
