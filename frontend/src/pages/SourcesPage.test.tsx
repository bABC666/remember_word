import { readFileSync } from 'node:fs'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'

const json = (value: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }))

const ADMIN = {
  id: 1, username: 'admin', display_name: 'admin', role: 'admin', is_admin: true,
  settings: { daily_new_words: 15, article_length: 650, onboarding_seen: true },
}

/** Three lexicons: a public imported one, the caller's own, and an empty one. */
const LEXICONS = [
  {
    id: 11, name: '考研核心词库', description: '公共导入词库', visibility: 'public', source_type: 'kaoyan',
    is_system: true, owner_user_id: null, can_write: false, entry_count: 3200, enabled: true,
    created_at: '2026-09-25T00:00:00Z',
  },
  {
    id: 12, name: '我的私人词库', description: '', visibility: 'private', source_type: 'manual',
    is_system: false, owner_user_id: 1, can_write: true, entry_count: 0, enabled: true,
    created_at: '2026-09-26T00:00:00Z',
  },
  {
    id: 13, name: '空词库', description: '', visibility: 'private', source_type: 'manual',
    is_system: false, owner_user_id: 1, can_write: true, entry_count: 0, enabled: true,
    created_at: '2026-09-26T00:00:00Z',
  },
]

/**
 * One source artifact as the endpoint reports it.
 *
 * `declared_approval_state` and the review block are the *declaration* an
 * administrator recorded; nothing here is a verified right to publish.
 */
function source(overrides: Record<string, unknown> = {}) {
  return {
    source_artifact_id: 3, role: 'primary', name: 'primary.csv',
    publisher: '词表发布方甲', version: '2026-09-25',
    obtained_at_utc: '2026-09-25T02:11:00Z', format: 'delimited',
    license_id: 'CC-BY-SA-4.0', license_text_sha256: 'a'.repeat(64),
    file_sha256: 'b'.repeat(64), mapping_sha256: 'c'.repeat(64), byte_size: 1234,
    use_scope: '预演草案·未获批准·仅内部测试', display_scope: '预演草案·未获批准·不对外展示',
    declared_approval_state: 'explicitly_unapproved',
    runs: [{ run_id: 'run-2026-09-25', confirmed_at: '2026-09-25T02:12:00Z', outcome: 'created' }],
    ...overrides,
  }
}

const SOURCES_BY_LEXICON: Record<number, { sources: unknown[]; review: unknown }> = {
  11: {
    sources: [
      source(),
      source({
        source_artifact_id: 4, role: 'meaning', name: 'supplement.csv', publisher: '词表发布方乙',
        license_id: 'CC-BY-NC-SA-4.0', license_text_sha256: 'd'.repeat(64),
        declared_approval_state: 'not_assessed',
      }),
      source({
        source_artifact_id: 5, role: 'meaning', name: 'dual.csv', publisher: '词表发布方丙',
        license_id: 'CC-BY-SA-4.0+GFDL', license_text_sha256: 'e'.repeat(64),
        declared_approval_state: 'not_assessed',
      }),
      source({
        source_artifact_id: 6, role: 'meaning', name: 'internal.csv', publisher: '词表发布方丁',
        license_id: 'proprietary-internal', license_text_sha256: 'f'.repeat(64),
        declared_approval_state: 'not_assessed',
      }),
    ],
    review: {
      status: 'pending_owner_approval', pending_sources: ['primary.csv'],
      message: '来源声明明确写明未获批准；仍待负责人批准。本接口只回放已记录的声明，不核实许可有效性。',
    },
  },
  12: {
    sources: [
      // The same artifact the first lexicon uses, imported into this one as well:
      // sharing a source file between lexicons is what the backend allows, and it is
      // what makes a bare `source-<artifact>` anchor ambiguous.
      source({ runs: [{ run_id: 'run-private-lexicon', confirmed_at: '2026-09-26T03:00:00Z', outcome: 'reused' }] }),
      source({
        source_artifact_id: 7, role: 'primary', name: 'mine.csv', publisher: '私人整理来源',
        license_id: '', license_text_sha256: '',
      }),
    ],
    review: {
      status: 'not_assessed', pending_sources: [],
      message: '以下许可与范围字段来自导入时记录的来源声明；本接口未核实许可真实性，也不代表授权已获确认。',
    },
  },
  13: {
    sources: [],
    review: {
      status: 'not_assessed', pending_sources: [],
      message: '以下许可与范围字段来自导入时记录的来源声明；本接口未核实许可真实性，也不代表授权已获确认。',
    },
  },
}

function routes(url: string, { signedIn = true }: { signedIn?: boolean } = {}) {
  if (url.includes('/api/auth/me')) {
    return signedIn ? json(ADMIN) : json({ detail: '请先登录' }, 401)
  }
  if (url.includes('/api/auth/logout')) return json({ ok: true })
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
  if (url.endsWith('/api/dashboard')) {
    return json({ today_new: 0, due_reviews: 0, weak_words: 0, reading_status: 'not_generated', streak_days: 0 })
  }
  if (url.endsWith('/api/imports')) return json([])
  const lexiconSources = /\/api\/lexicons\/(\d+)\/sources$/.exec(url)
  if (lexiconSources) {
    const id = Number(lexiconSources[1])
    const entry = SOURCES_BY_LEXICON[id]
    const lexicon = LEXICONS.find((row) => row.id === id)
    return json({
      lexicon: { id, name: lexicon?.name ?? '未知词库' },
      sources: entry?.sources ?? [],
      authorization_review: entry?.review ?? { status: 'not_assessed', pending_sources: [], message: '' },
    })
  }
  if (url.endsWith('/api/lexicons')) return json(LEXICONS)
  return json({ total: 0, words: [] })
}

/** Stub the breakpoint the shell asks about: `compact` is the F-6 phone bar. */
function stubViewport({ compact }: { compact: boolean }) {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    matches: query.includes('620px') ? compact : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })))
}

function mockFetch(options: { signedIn?: boolean } = {}) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => routes(String(input), options)))
}

async function openSources() {
  render(<App />)
  await screen.findByRole('navigation', { name: '主导航' })
}

function lexiconSection(name: string) {
  return screen.getByRole('region', { name })
}

/** Each lexicon reads its own sources, so a card appears one request after the section. */
async function sourceCard(publisher: string) {
  return screen.findByRole('article', { name: new RegExp(publisher) })
}

beforeEach(() => {
  window.history.pushState({}, '', '/sources')
  stubViewport({ compact: false })
  mockFetch()
})

describe('the sources route', () => {
  it('keeps the page and its data behind the sign-in gate', async () => {
    mockFetch({ signedIn: false })
    render(<App />)

    expect(await screen.findByRole('form', { name: '登录拾词' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '数据来源与许可' })).not.toBeInTheDocument()
    // The gate has to hold for the data too, not only for the markup: no request
    // for a lexicon or its sources may leave the browser before sign-in.
    const requested = vi.mocked(fetch).mock.calls.map(([input]) => String(input))
    expect(requested.some((url) => url.includes('/api/lexicons'))).toBe(false)
  })

  it('reaches the page from the settings section', async () => {
    window.history.pushState({}, '', '/settings')
    await openSources()
    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: /数据来源与许可/ }))

    expect(await screen.findByRole('heading', { name: '数据来源与许可' })).toBeInTheDocument()
    expect(await screen.findByRole('region', { name: '考研核心词库' })).toBeInTheDocument()
  })
})

describe('what the page shows', () => {
  it('lists every accessible lexicon with the sources recorded for it', async () => {
    await openSources()

    const publicLexicon = await screen.findByRole('region', { name: '考研核心词库' })
    const ownLexicon = lexiconSection('我的私人词库')

    // Each lexicon's own sources, and only those: a source recorded for one lexicon
    // and not the other must not appear under both.
    expect(await within(publicLexicon).findByRole('article', { name: /词表发布方乙/ })).toBeInTheDocument()
    expect(await within(ownLexicon).findByRole('article', { name: /私人整理来源/ })).toBeInTheDocument()
    expect(within(publicLexicon).queryByText('私人整理来源')).not.toBeInTheDocument()
    expect(within(ownLexicon).queryByText('补充来源整理者')).not.toBeInTheDocument()

    // The per-source anchor is what a word detail block links to, and it carries the
    // lexicon as well as the artifact, so the same artifact can be a card in two
    // lexicons without either one shadowing the other.
    expect(document.getElementById('source-11-3')).toBeInTheDocument()
    expect(document.getElementById('source-11-4')).toBeInTheDocument()
    expect(document.getElementById('source-12-7')).toBeInTheDocument()
  })

  it('scrolls to the card the fragment names once that card exists', async () => {
    // The card is rendered only after this lexicon's sources arrive, which is later
    // than the browser's own fragment scroll -- so arriving from a word detail would
    // leave the reader at the top of the page. jsdom has no scrolling of its own, so
    // the call itself is what is pinned here; the real browser is the walkthrough.
    const original = Element.prototype.scrollIntoView
    const scrolled: string[] = []
    Element.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this.id) }
    try {
      window.history.pushState({}, '', '/sources#source-12-3')
      await openSources()

      await waitFor(() => expect(scrolled).toContain('source-12-3'))
      // Only the named card: a page that scrolled the first card would land the reader
      // in the wrong lexicon's section, which is the bug this anchor form fixes.
      expect(scrolled).toEqual(['source-12-3'])
    } finally {
      Element.prototype.scrollIntoView = original
    }
  })

  it.each(['%', '%E0%A4%A'])('ignores an undecodable fragment %s and keeps the page visible', async (fragment) => {
    const original = Element.prototype.scrollIntoView
    const scrolled: string[] = []
    Element.prototype.scrollIntoView = function scrollIntoView() { scrolled.push(this.id) }
    try {
      window.history.pushState({}, '', `/sources#${fragment}`)
      await openSources()
      expect(await screen.findByRole('heading', { name: '数据来源与许可' })).toBeInTheDocument()
      expect(await screen.findByRole('article', { name: /词表发布方甲/ })).toBeInTheDocument()
      expect(scrolled).toEqual([])
    } finally {
      Element.prototype.scrollIntoView = original
    }
  })

  it('gives each lexicon its own anchor when one artifact is shared', async () => {
    await openSources()

    // Each lexicon reads its own sources: wait until the shared artifact has a card in
    // both, rather than reading the page while the second query is still in flight.
    await waitFor(() => {
      expect(document.getElementById('source-11-3')).not.toBeNull()
      expect(document.getElementById('source-12-3')).not.toBeNull()
    })

    const sharedIds = [...document.querySelectorAll('.source-card')]
      .filter((card) => card.textContent?.includes('词表发布方甲'))
      .map((card) => card.id)
    expect(sharedIds).toEqual(['source-11-3', 'source-12-3'])

    // Each card belongs to the lexicon whose section holds it, so an anchor resolves
    // to the reader's own lexicon instead of whichever card comes first.
    const sectionOf = (id: string) =>
      document.getElementById(id)?.closest('.lexicon-section')?.querySelector('h2')?.textContent
    expect(sectionOf('source-11-3')).toBe('考研核心词库')
    expect(sectionOf('source-12-3')).toBe('我的私人词库')

    // Nothing on the page may carry an id twice: a duplicate id makes the second card
    // unreachable as a fragment target, which is exactly how the internal link used to
    // land in the wrong lexicon's section.
    const ids = [...document.querySelectorAll('[id]')].map((node) => node.id)
    expect(ids.length).toBeGreaterThan(10)
    expect(ids.filter((id, index) => ids.indexOf(id) !== index)).toEqual([])
  })

  it('states plainly that a lexicon has recorded no sources', async () => {
    await openSources()

    const empty = await screen.findByRole('region', { name: '空词库' })
    expect(await within(empty).findByText('该词库还没有记录任何来源')).toBeInTheDocument()
    expect(within(empty).queryByRole('article')).not.toBeInTheDocument()
    // "No records" must not spread to the lexicons that do have them.
    const publicLexicon = lexiconSection('考研核心词库')
    expect(await within(publicLexicon).findByRole('article', { name: /词表发布方甲/ })).toBeInTheDocument()
  })

  it('explains itself when the account has no lexicon at all', async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/lexicons')) return json([])
      return routes(url)
    })
    await openSources()

    expect(await screen.findByText('还没有可查看的词库')).toBeInTheDocument()
    expect(document.querySelectorAll('.lexicon-section')).toHaveLength(0)
  })

  it('reports the recorded declaration state and the review notice as they are', async () => {
    await openSources()

    // Scoped to the first lexicon: the same artifact is a card in the second one too.
    const sharedCard = await within(await screen.findByRole('region', { name: '考研核心词库' }))
      .findByRole('article', { name: /词表发布方甲/ })
    expect(within(sharedCard).getByText('声明写明未获批准')).toBeInTheDocument()
    expect(await within(await sourceCard('词表发布方乙')).findByText('尚未评估')).toBeInTheDocument()

    const publicLexicon = lexiconSection('考研核心词库')
    expect(within(publicLexicon).getByText(/仍待负责人批准/)).toBeInTheDocument()

    // A lexicon with nothing recorded still carries the review notice, so an empty
    // list cannot read as "cleared".
    const empty = lexiconSection('空词库')
    expect(await within(empty).findByText(/未核实许可真实性/)).toBeInTheDocument()
  })

  it('links a known single licence identifier to its canonical page', async () => {
    await openSources()

    const bySa = within(await screen.findByRole('region', { name: '考研核心词库' }))
    expect(await bySa.findByRole('link', { name: /CC-BY-SA-4\.0/ })).toHaveAttribute(
      'href', 'https://creativecommons.org/licenses/by-sa/4.0/',
    )
    expect(bySa.getByRole('link', { name: /CC-BY-NC-SA-4\.0/ })).toHaveAttribute(
      'href', 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
    )
  })

  it('shows a composite or unknown identifier as text without guessing a link', async () => {
    await openSources()

    const dual = await sourceCard('词表发布方丙')
    expect(within(dual).getByText('CC-BY-SA-4.0+GFDL')).toBeInTheDocument()
    expect(within(dual).queryByRole('link')).not.toBeInTheDocument()

    const unknown = await sourceCard('词表发布方丁')
    expect(within(unknown).getByText('proprietary-internal')).toBeInTheDocument()
    expect(within(unknown).queryByRole('link')).not.toBeInTheDocument()

    // An identifier nobody recorded is shown as "not recorded", not as a guess.
    const missing = await sourceCard('私人整理来源')
    expect(within(missing).getByText('未记录', { selector: '.license-value' })).toBeInTheDocument()
    expect(within(missing).queryByRole('link')).not.toBeInTheDocument()
  })

  it('never phrases a recorded declaration as a grant or as a restriction on rights', async () => {
    await openSources()
    // Read the whole page, source cards included: the phrases to avoid would appear
    // in the per-source text, so scanning a half-rendered page would prove nothing.
    await sourceCard('词表发布方丁')
    await sourceCard('私人整理来源')
    const page = document.querySelector('.sources-page')!
    const text = page.textContent ?? ''

    // Writing a declaration up as a verified grant is the one thing the display
    // design forbids outright, and its banned wording is banned even inside a
    // negation: a page that has to say "this is not 已获授权" has already put the
    // phrase in front of a reader.
    for (const claim of ['已获授权', '符合 CC 要求', '官方授权']) {
      expect(text, claim).not.toContain(claim)
    }
    // And not offering the source file is a fact about this page, never a limit on
    // what a licence lets a reader do.
    for (const restriction of ['不得下载', '禁止下载', '不得转载', '禁止转载', '无权使用', '不得使用']) {
      expect(text, restriction).not.toContain(restriction)
    }
    // The page says what it is: a transcript of what was declared.
    expect(text).toContain('不代表授权已获确认')
  })
})

describe('the navigation', () => {
  const DESTINATIONS = ['今日概览', '单词导入', '今日学习', '阅读练习', '我的词库', '设置']

  it('keeps the six desktop destinations and adds no seventh entry', async () => {
    await openSources()

    const nav = screen.getByRole('navigation', { name: '主导航' })
    for (const label of DESTINATIONS) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument()
    }
    expect(nav.querySelectorAll('.nav-link')).toHaveLength(6)
    expect(within(nav).queryByRole('link', { name: /数据来源/ })).not.toBeInTheDocument()
  })

  it('keeps the five phone bar entries', async () => {
    stubViewport({ compact: true })
    await openSources()

    const nav = screen.getByRole('navigation', { name: '主导航' })
    expect(nav.querySelectorAll('.nav-link')).toHaveLength(5)
    expect(within(nav).queryByRole('link', { name: /数据来源/ })).not.toBeInTheDocument()
  })
})

describe('the stylesheet', () => {
  const styles = readFileSync('src/styles.css', 'utf-8')

  it('gives the sources grids shrinkable tracks', () => {
    // A bare `1fr` is `minmax(auto, 1fr)`: its min-content floor is what widened
    // the settings page to 961px on a 390px phone (G8).
    for (const selector of ['.sources-page', '.lexicon-sources', '.source-cards', '.source-meta']) {
      const pattern = new RegExp(selector.replace(/\./g, '\\.') + '\\s*\\{([^}]*)\\}', 'g')
      const blocks = [...styles.matchAll(pattern)].map((match) => match[1])
      expect(blocks.length, selector).toBeGreaterThan(0)
      for (const block of blocks) {
        expect(block, selector).not.toMatch(/grid-template-columns:\s*1fr\b/)
        expect(block, selector).not.toMatch(/grid-template-columns:\s*repeat\(\d+,\s*1fr\)/)
      }
    }
  })
})
