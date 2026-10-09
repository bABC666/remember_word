import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReadingPage } from './ReadingPage'
import type { Word } from '../types'

const article = {
  id: 7,
  title: 'Memory and Cities',
  content: 'Cities retain traces of the people who built them.',
  created_at: '2026-09-22T00:00:00Z',
  target_words: ['retain'],
  completed: false,
  translation: '',
  translated_at: null,
  lookup_history: [],
}

function json(data: unknown) {
  return Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } }))
}

afterEach(() => vi.restoreAllMocks())

describe('ReadingPage assistance', () => {
  it('looks up a clicked word and keeps an on-demand translation', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url === '/api/articles') return json([article])
      if (url.endsWith('/lookup')) return json({ id: 11, article_id: 7, surface: 'retain', normalized_word: 'retain', phonetic: '/rɪˈteɪn/', part_of_speech: 'v.', meaning: '保留', explanation: '在此处指城市保留痕迹。', context: article.content, source: 'wordbook', added_word_id: 3, created_at: article.created_at })
      if (url.endsWith('/translate')) return json({ ...article, translation: '城市保留了建造者留下的痕迹。', translated_at: article.created_at, lookup_history: [] })
      if (url === '/api/articles/7') return json(article)
      throw new Error(`unexpected request ${url} ${init?.method ?? 'GET'}`)
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter><ReadingPage /></MemoryRouter></QueryClientProvider>)

    fireEvent.click(await screen.findByRole('button', { name: 'retain' }))
    expect(await screen.findByText('保留')).toBeInTheDocument()
    expect(screen.getByText('/rɪˈteɪn/')).toBeInTheDocument()
    expect(screen.getByText('已在词库')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '显示全文翻译' }))
    expect(await screen.findByText('城市保留了建造者留下的痕迹。')).toBeInTheDocument()
  })
})

const quizWord: Word & { context: string } = {
  id: null, word_state_id: 42, legacy_word_id: null, lexicon_entry_id: 7,
  lexicon_id: 1, word: 'rural', phonetic: '', part_of_speech: 'adj.',
  source_meanings: ['來源農村的'], source_raw: 'rural adj. 來源農村的',
  meaning_origin: 'platform', concise_meanings: [], anchor: '乡村', semantic_note: '',
  status: 'new', first_seen: '2026-09-22T00:00:00Z', last_review: null,
  next_review_at: null, recall_success: 0, recall_fail: 0, context_exposure: 0,
  possible_issue: false, notes: '', context: 'a rural town',
}

async function renderQuiz(word: Word & { context: string }) {
  const completed = { ...article, completed: true, quiz_words: [word] }
  vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input)
    if (url === '/api/articles') return json([completed])
    if (url === '/api/articles/7') return json(completed)
    throw new Error(`unexpected request ${url}`)
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><ReadingPage /></MemoryRouter></QueryClientProvider>)
  await userEvent.type(await screen.findByRole('textbox', { name: '写下你在当前语境中理解的意思' }), '我的理解')
  await userEvent.click(screen.getByRole('button', { name: '核对答案' }))
}

describe('ReadingPage meaning authenticity', () => {
  it('shows evidence-confirmed short meaning separately from source text', async () => {
    await renderQuiz({ ...quizWord, concise_meanings: [{
      pos_key: 'adj', pos_label: '形容词', pos_source: 'pos_section',
      pos_source_label: '来源小节标题', pos_order: 1, meanings: [{
        text: '农村的', display_order: 1, provenance_kind: 'derived',
        provenance_label: '据来源改写', is_source_verbatim: false, is_supplement: false,
        source_locator: 'zhwiktionary:12', source_evidence_id: null, citations: [],
        derivation_note: '简体转换', confirmed_by: 'owner', confirmed_at: '2026-10-03T00:00:00Z',
      }],
    }] })
    expect(screen.getByRole('region', { name: '核心释义' })).toHaveTextContent('农村的')
    expect(screen.getByText('查看来源原文（未确认短义）')).toBeInTheDocument()
    expect(screen.getByText('來源農村的')).not.toBeVisible()
  })

  it('does not turn source-only text into a confirmed answer', async () => {
    await renderQuiz(quizWord)
    expect(screen.getByText('暂无已确认的核心释义')).toBeInTheDocument()
    expect(screen.getByText('來源農村的')).not.toBeVisible()
    await userEvent.click(screen.getByText('查看来源原文（未确认短义）'))
    expect(screen.getByText('來源農村的')).toBeVisible()
    expect(screen.getByRole('link', { name: '查看词条来源记录' })).toHaveAttribute('href', '/library/42')
  })

  it("labels a user's imported meaning unverified", async () => {
    await renderQuiz({ ...quizWord, meaning_origin: 'user_provided', source_meanings: ['用户释义'], source_raw: '' })
    expect(screen.getByText('查看用户提供的释义（未核实）')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '核心释义' })).toBeNull()
  })

  it('shows an empty state when no meaning exists', async () => {
    await renderQuiz({ ...quizWord, source_meanings: [], source_raw: '' })
    expect(screen.getByText('暂无可用释义')).toBeInTheDocument()
    expect(screen.queryByText(/查看来源原文/)).toBeNull()
  })
})
