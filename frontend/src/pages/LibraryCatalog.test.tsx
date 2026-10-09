import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { App } from '../App'

const freshWord = {
  id: null, word_state_id: null, legacy_word_id: null, lexicon_entry_id: 701,
  lexicon_id: 5, word: 'untouched', phonetic: '', part_of_speech: 'adj.',
  source_meanings: ['尚未触碰的'], source_raw: '', concise_meanings: [],
  anchor: '', semantic_note: '', status: 'new', first_seen: null, last_review: null,
  next_review_at: null, recall_success: 0, recall_fail: 0, context_exposure: 0,
  possible_issue: false, notes: '',
}

function setup(mobile = false) {
  const calls: string[] = []
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: mobile, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    if (url === '/api/auth/me') return Promise.resolve(Response.json({ id: 1, username: 'tester', display_name: 'tester', role: 'user', is_admin: false }))
    if (url === '/api/settings/onboarding') return Promise.resolve(Response.json({ seen: true }))
    if (url.startsWith('/api/words?')) {
      const params = new URL(url, 'http://test').searchParams
      const next = params.get('offset') === '50'
      return Promise.resolve(Response.json({ total: params.get('status') === 'unstudied' ? 1 : 51,
        lexicon: { id: 5, name: 'NETEM' }, words: [{ ...freshWord,
          lexicon_entry_id: next ? 702 : 701, word: next ? 'second-page' : 'untouched',
        }] }))
    }
    if (url === '/api/words/entry/701') return Promise.resolve(Response.json({ ...freshWord,
      review_history: [], article_exposures: [], sources: {
        fields: [], completeness: { status: 'incomplete', missing: [] },
      },
    }))
    throw new Error('Unexpected request: ' + url)
  }))
  return calls
}

beforeEach(() => {
  window.history.replaceState({}, '', '/library')
  vi.stubGlobal('scrollTo', vi.fn())
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('shows the selected catalog and opens unstudied entry details on desktop', async () => {
  const calls = setup()
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: /untouched/ }))
  expect(await screen.findByRole('heading', { name: 'untouched' })).toBeInTheDocument()
  expect(screen.getByText(/当前词库：NETEM/)).toBeInTheDocument()
  expect(screen.getByText('51 个词条')).toBeInTheDocument()
  expect(calls.some((url) => url.includes('catalog=true'))).toBe(true)
  expect(calls).toContain('/api/words/entry/701')
  expect(calls.some((url) => url.includes('/api/study/'))).toBe(false)
})

it('pages through all entries and resets the page for the unstudied filter', async () => {
  const calls = setup()
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: '下一页' }))
  expect(await screen.findByRole('button', { name: /second-page/ })).toBeInTheDocument()
  expect(calls.at(-1)).toContain('offset=50')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: '学习状态' }), 'unstudied')
  await waitFor(() => expect(calls.at(-1)).toContain('status=unstudied'))
  expect(calls.at(-1)).toContain('offset=0')
  expect(screen.queryByRole('heading', { name: 'untouched' })).not.toBeInTheDocument()
})

it('opens an unstudied entry on mobile and returns to the filtered list', async () => {
  const calls = setup(true)
  window.history.replaceState({}, '', '/library?status=unstudied')
  render(<App />)
  const row = await screen.findByRole('link', { name: /untouched/ })
  expect(row).toHaveAttribute('href', '/library/entry/701')
  await userEvent.click(row)
  expect(await screen.findByRole('heading', { name: 'untouched' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('link', { name: '返回词库' }))
  expect(await screen.findByRole('link', { name: /untouched/ })).toHaveFocus()
  expect(window.location.search).toBe('?status=unstudied')
  expect(calls).toContain('/api/words/entry/701')
})
