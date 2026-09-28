import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { ConciseMeaning, ConciseMeaningGroup, Word } from '../types'

/**
 * What the study page shows for a word that has confirmed short meanings, and what it
 * shows when it does not.
 *
 * The things under test are the product rules for this round: the short senses come
 * first and are grouped by part of speech, the source text is still there to check them
 * against, and the source's own single `part_of_speech` string does not compete with the
 * group headings. The last is the rule that keeps an unconfirmed draft off the page --
 * modelled here as the server answering with an empty list, which is exactly what it
 * does, and which the page must not paper over with anything unreviewed.
 */

function word(overrides: Partial<Word> = {}): Word {
  return {
    id: null,
    word_state_id: 42,
    legacy_word_id: null,
    lexicon_entry_id: 7,
    lexicon_id: 1,
    word: 'rural',
    phonetic: '/ˈrʊərəl/',
    part_of_speech: 'adj.',
    source_meanings: ['農村的'],
    source_raw: 'rural adj. 農村的',
    concise_meanings: [],
    anchor: '乡村',
    semantic_note: '',
    status: 'new',
    first_seen: '2026-09-26T00:00:00Z',
    last_review: null,
    next_review_at: null,
    recall_success: 0,
    recall_fail: 0,
    context_exposure: 0,
    possible_issue: false,
    notes: '',
    ...overrides,
  }
}

function short(overrides: Partial<ConciseMeaning> = {}): ConciseMeaning {
  return {
    text: '农村的',
    display_order: 1,
    provenance_kind: 'derived',
    provenance_label: '据来源改写',
    is_source_verbatim: false,
    is_supplement: false,
    source_locator: 'zhwiktionary:12',
    source_evidence_id: null,
    citations: [],
    derivation_note: '来源为「農村的」，此处为简体转换',
    confirmed_by: 'owner',
    confirmed_at: '2026-09-26T00:00:00Z',
    ...overrides,
  }
}

function group(overrides: Partial<ConciseMeaningGroup> = {}): ConciseMeaningGroup {
  return {
    pos_key: 'adj',
    pos_label: '形容词',
    pos_source: 'pos_section',
    pos_source_label: '来源小节标题',
    pos_order: 1,
    meanings: [short()],
    ...overrides,
  }
}

function mockApi(words: Word[]) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/auth/me') {
      return Promise.resolve(Response.json({
        id: 1, username: 'owner', display_name: 'owner', role: 'admin', is_admin: true,
        settings: { daily_new_words: 15, article_length: 650, onboarding_seen: true },
      }))
    }
    if (url === '/api/settings/onboarding') return Promise.resolve(Response.json({ seen: true }))
    if (url === '/api/study/today') {
      return Promise.resolve(Response.json({
        total: words.length, words,
        daily_new_words: { target: 15, consumed_today: 0, remaining: 15 },
      }))
    }
    if (url.startsWith('/api/study/word-states/')) return Promise.resolve(Response.json({ ok: true }))
    if (url === '/api/dashboard') {
      return Promise.resolve(Response.json({ today_new: 0, due_reviews: 0, weak_words: 0, reading_status: 'not_generated', streak_days: 0 }))
    }
    throw new Error('Unexpected request: ' + url)
  }))
}

beforeEach(() => {
  window.history.replaceState({}, '', '/study')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function reveal() {
  render(<App />)
  await userEvent.click(await screen.findByRole('button', { name: /显示答案/ }))
}

describe('the study page and short confirmed meanings', () => {
  it('shows the confirmed short value first and keeps the source reachable', async () => {
    mockApi([word({ concise_meanings: [group()] })])
    await reveal()

    const block = await screen.findByRole('region', { name: '核心释义' })
    expect(block).toHaveTextContent('农村的')
    expect(block).toHaveTextContent('据来源改写')
    expect(block).toHaveTextContent('来源为「農村的」，此处为简体转换')
    expect(block).toHaveTextContent('已由 owner 人工确认')

    // The source is not gone: it is one click away, and it still says 農村的.
    const source = document.querySelector<HTMLDetailsElement>('.study-source')!
    expect(source).toBeTruthy()
    expect(source.textContent).toContain('農村的')
    expect(source.textContent).toContain('原书完整释义')
    expect(source.open).toBe(false)  // collapsed, not removed: "可回查", not "always shown"
  })

  it('falls back to the anchor and the source when nothing is confirmed', async () => {
    mockApi([word()])
    await reveal()

    // Exactly what the page showed before short meanings existed.
    expect((await screen.findAllByText('最小语义锚点')).length).toBeGreaterThan(0)
    expect(screen.getByText('乡村')).toBeInTheDocument()
    expect(screen.getByText('農村的')).toBeInTheDocument()
    expect(document.querySelector('.study-source')).toBeNull()
  })

  it('never invents a displayed value for an unconfirmed draft', async () => {
    // The server answers [] while a candidate exists -- which is what an
    // unconfirmed proposal looks like from the client's side.
    mockApi([word({
      source_meanings: ['農村的'],
      concise_meanings: [],
    })])
    await reveal()

    expect(await screen.findByText('農村的')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '核心释义' })).toBeNull()
    expect(screen.queryByText('已由 owner 人工确认')).toBeNull()
    expect(screen.queryByText('农村的')).toBeNull()
  })

  it('announces a self-authored supplement as a supplement', async () => {
    mockApi([word({
      concise_meanings: [group({
        meanings: [short({
          text: '看似', provenance_kind: 'ai_supplement', provenance_label: '自拟补充',
          is_supplement: true, source_locator: '',
          derivation_note: '现有来源只有较少见义项，补充常见义',
        })],
      })],
    })])
    await reveal()

    const block = await screen.findByRole('region', { name: '核心释义' })
    expect(block).toHaveTextContent('看似')
    expect(block).toHaveTextContent('自拟补充')
    expect(block).not.toHaveTextContent('来源位置')
  })

  it('keeps the review keys working with a short meaning on screen', async () => {
    mockApi([word({ concise_meanings: [group()] })])
    await reveal()
    await screen.findByRole('region', { name: '核心释义' })

    await userEvent.keyboard('3')
    await waitFor(() => {
      expect(screen.getByText('今天暂时没有待学习的单词')).toBeInTheDocument()
    })
  })

  it('shows one group per part of speech, in group order, marking a judged one', async () => {
    // `play`: three verb senses and one noun sense. The verb group's part of speech was
    // a person's judgement, so its heading has to say so.
    mockApi([word({
      word: 'play',
      phonetic: '/pleɪ/',
      concise_meanings: [
        group({
          pos_key: 'verb', pos_label: '动词', pos_order: 1,
          pos_source: 'reviewer', pos_source_label: '人工试判',
          meanings: [
            short({ text: '玩', display_order: 1 }),
            short({ text: '演奏', display_order: 2 }),
            short({ text: '播放', display_order: 3 }),
          ],
        }),
        group({
          pos_key: 'noun', pos_label: '名词', pos_order: 2,
          meanings: [short({ text: '剧', display_order: 1 })],
        }),
      ],
    })])
    await reveal()

    const block = await screen.findByRole('region', { name: '核心释义' })
    expect([...block.querySelectorAll('.concise-pos')].map((n) => n.textContent)).toEqual([
      '动词', '名词',
    ])
    expect(block.querySelectorAll('.concise-pos-basis')).toHaveLength(1)
    expect(block).toHaveTextContent('词性试判')
    expect(
      [...block.querySelectorAll('.concise-text')].map((n) => n.textContent),
    ).toEqual(['玩', '演奏', '播放', '剧'])
  })

  it('hides the source’s single part-of-speech chip when the values carry groups', async () => {
    // `lexicon_entry.part_of_speech` is one source-declared string. Beside group
    // headings it would be a second, possibly different claim about the same thing.
    mockApi([word({ concise_meanings: [group()] })])
    await reveal()
    await screen.findByRole('region', { name: '核心释义' })
    expect(screen.queryByText('adj.')).toBeNull()
  })

  it('keeps the part-of-speech chip when there is nothing grouped to show instead', async () => {
    mockApi([word()])
    await reveal()
    expect(await screen.findByText('adj.')).toBeInTheDocument()
  })
})
