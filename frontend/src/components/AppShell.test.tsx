import { readFileSync } from 'node:fs'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'

const json = (value: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }))

const DESTINATIONS = ['今日概览', '单词导入', '今日学习', '阅读练习', '我的词库', '设置']

function routes(url: string) {
  if (url.includes('/api/auth/me')) {
    return json({
      id: 1, username: 'admin', display_name: 'admin', role: 'admin', is_admin: true,
      settings: { daily_new_words: 15, article_length: 650, onboarding_seen: true },
    })
  }
  if (url.includes('/api/auth/logout')) return json({ ok: true })
  if (url.includes('/api/auth/login')) return json({ ok: true })
  if (url.includes('/api/settings/onboarding')) return json({ seen: true })
  if (url.endsWith('/api/auth/sessions')) return json({ sessions: [] })
  if (url.endsWith('/api/settings')) {
    return json({
      deepseek_api_key_configured: false, deepseek_api_key_masked: '', deepseek_base_url: 'https://api.deepseek.com',
      deepseek_model: 'deepseek-flash', deepseek_model_display: 'DeepSeek V4.1-Flash', daily_new_words: 15,
      article_length: 650, ocr_language: 'en', ocr_use_gpu: false, paddleocr_available: false,
      paddleocr_message: '未安装', data_directory: 'D:/data', database_path: 'D:/data/vocab.db',
      backups_directory: 'D:/data/backups', can_manage_instance_settings: true, onboarding_seen: true,
    })
  }
  if (url.endsWith('/api/imports')) return json([])
  if (url.endsWith('/api/dashboard')) {
    return json({ today_new: 0, due_reviews: 0, weak_words: 0, reading_status: 'not_generated', streak_days: 0 })
  }
  return json({ total: 0, words: [] })
}

/**
 * Stub the two breakpoints the shell asks about.
 *
 * ``compact`` is the F-6 bottom bar (<=620px); ``rail`` is the tablet icon rail
 * (<=900px), which keeps all six destinations.
 */
function stubViewport({ compact, rail = false }: { compact: boolean; rail?: boolean }) {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    matches: query.includes('620px') ? compact : query.includes('900px') ? (compact || rail) : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })))
}

async function signIn() {
  render(<App />)
  // The navigation only exists past the sign-in gate, and unlike a page greeting
  // it is there whatever route the test starts on.
  await screen.findByRole('navigation', { name: '主导航' })
}

function mainNav() {
  return screen.getByRole('navigation', { name: '主导航' })
}

function moreSheet() {
  return screen.getByRole('dialog', { name: '更多' })
}

async function openMore() {
  await userEvent.click(within(mainNav()).getByRole('button', { name: '更多' }))
  return moreSheet()
}

beforeEach(() => {
  window.history.pushState({}, '', '/')
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => routes(String(input))))
  stubViewport({ compact: false })
})

describe('desktop and tablet shell', () => {
  it('keeps all six destinations, the help entry and sign-out', async () => {
    await signIn()

    for (const label of DESTINATIONS) {
      expect(within(mainNav()).getByRole('link', { name: label })).toBeInTheDocument()
    }
    expect(within(mainNav()).queryByRole('button', { name: '更多' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '更多' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '使用说明' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '退出登录' })).toBeInTheDocument()
  })

  it('keeps all six destinations on the tablet icon rail', async () => {
    stubViewport({ compact: false, rail: true })
    await signIn()

    expect(mainNav().querySelectorAll('.nav-link')).toHaveLength(6)
    expect(within(mainNav()).queryByRole('button', { name: '更多' })).not.toBeInTheDocument()
  })
})

describe('phone bottom bar (F-6)', () => {
  beforeEach(() => {
    stubViewport({ compact: true })
  })

  it('offers exactly five entries: four destinations plus 更多', async () => {
    await signIn()

    expect(mainNav().querySelectorAll('.nav-link')).toHaveLength(5)
    for (const label of ['今日概览', '今日学习', '阅读练习', '我的词库']) {
      expect(within(mainNav()).getByRole('link', { name: label })).toBeInTheDocument()
    }
    // The two destinations the sheet owns must not also sit in the bar.
    expect(within(mainNav()).queryByRole('link', { name: '单词导入' })).not.toBeInTheDocument()
    expect(within(mainNav()).queryByRole('link', { name: '设置' })).not.toBeInTheDocument()
    const more = within(mainNav()).getByRole('button', { name: '更多' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    expect(more).toHaveAttribute('aria-controls', 'mobile-more')
  })

  it('reaches import, settings, help and sign-out from the sheet', async () => {
    await signIn()
    const sheet = await openMore()

    expect(within(mainNav()).getByRole('button', { name: '更多' })).toHaveAttribute('aria-expanded', 'true')
    expect(within(sheet).getByRole('link', { name: '单词导入' })).toBeInTheDocument()
    expect(within(sheet).getByRole('link', { name: '设置' })).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: '使用说明' })).toBeInTheDocument()
    expect(within(sheet).getByRole('button', { name: '退出登录' })).toBeInTheDocument()
    // The account the sheet acts on is visible in it, not behind it.
    expect(within(sheet).getByText(/admin/)).toBeInTheDocument()
  })

  it('signs out from the sheet', async () => {
    await signIn()
    const sheet = await openMore()

    await userEvent.click(within(sheet).getByRole('button', { name: '退出登录' }))

    expect(await screen.findByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/api/auth/logout'))).toBe(true)
  })

  it('opens the help dialog from the sheet', async () => {
    await signIn()
    const sheet = await openMore()

    await userEvent.click(within(sheet).getByRole('button', { name: '使用说明' }))

    expect(await screen.findByRole('dialog', { name: '欢迎使用拾词' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '更多' })).not.toBeInTheDocument()
  })

  it('closes on Escape, on the backdrop and on the close button', async () => {
    await signIn()

    await openMore()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: '更多' })).not.toBeInTheDocument()

    await openMore()
    await userEvent.click(document.querySelector('.more-backdrop')!)
    expect(screen.queryByRole('dialog', { name: '更多' })).not.toBeInTheDocument()

    await openMore()
    await userEvent.click(within(moreSheet()).getByRole('button', { name: '关闭更多' }))
    expect(screen.queryByRole('dialog', { name: '更多' })).not.toBeInTheDocument()
  })

  it('navigates and closes when a sheet link is used', async () => {
    await signIn()
    const sheet = await openMore()

    await userEvent.click(within(sheet).getByRole('link', { name: '单词导入' }))

    expect(await screen.findByRole('heading', { name: '从单词书导入' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '更多' })).not.toBeInTheDocument()
  })

  it('marks 更多 as current while a sheet destination is open', async () => {
    window.history.pushState({}, '', '/settings')
    await signIn()

    expect(within(mainNav()).getByRole('button', { name: '更多' })).toHaveClass('active')
  })
})

describe('phone bottom bar styles', () => {
  const styles = readFileSync('src/styles.css', 'utf-8')

  /** The body of every ``@media (max-width: 620px) { ... }`` block. */
  function compactBlocks(): string {
    return [...styles.matchAll(/@media \(max-width: 620px\) \{([^@]*)\}/g)].map((match) => match[1]).join('\n')
  }

  it('lays the bar out for five entries', () => {
    expect(compactBlocks()).toContain('grid-template-columns: repeat(5,1fr)')
    expect(styles).not.toContain('repeat(6,1fr)')
  })

  it('takes the overflowing account block and help entry out of the bar', () => {
    // Both used to be laid out inside the fixed 64px bar, which put the only
    // sign-out control below the viewport on a phone.
    expect(compactBlocks()).toContain('.sidebar-account { display: none; }')
    expect(compactBlocks()).toContain('.help-entry { display: none; }')
  })

  it('hides the account block with a rule that outranks the base rule', () => {
    // A media query adds no specificity: a `display: none` written before the
    // base `.sidebar-account { display: flex }` loses to it, which is exactly how
    // sign-out stayed in the overflowing bar.
    const base = styles.indexOf('.sidebar-account { display: flex')
    const hide = styles.indexOf('.sidebar-account { display: none; }')
    expect(base).toBeGreaterThan(-1)
    expect(hide).toBeGreaterThan(base)
  })

  it('opens the sheet above the bar, inside the safe area', () => {
    expect(styles).toContain('.more-sheet { position: fixed; z-index: 61; right: 10px; bottom: calc(72px + var(--safe-area-bottom));')
    expect(styles).toContain('inset: 0 0 calc(64px + var(--safe-area-bottom)) 0;')
    expect(styles).toContain('min-height: 48px;')
  })
})
