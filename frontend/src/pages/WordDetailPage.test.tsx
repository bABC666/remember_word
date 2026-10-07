import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { WordDetailContent } from './WordDetailPage'
import type { ConciseMeaning, ConciseMeaningGroup } from '../types'
import type { WordDetail } from './wordDetailModel'

/**
 * The word detail page's half of the grouped display.
 *
 * `WordDetailContent` is rendered directly rather than through the route, because the
 * route adds data fetching that is not what this slice is about; the two entry points
 * that use it (the detail page and the library's side panel) both render this
 * component, so its behaviour is the page's behaviour.
 *
 * The rules under test: groups appear in `pos_order` with their values in
 * `display_order`, a group whose part of speech a person judged says so, every source
 * position of a value is reachable, a self-authored supplement is never dressed as a
 * quotation, and the source's own single `part_of_speech` string gives way to the group
 * headings instead of competing with them.
 */

function meaning(overrides: Partial<ConciseMeaning> = {}): ConciseMeaning {
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
    meanings: [meaning()],
    ...overrides,
  }
}

function detail(overrides: Partial<WordDetail> = {}): WordDetail {
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
    status: 'weak',
    first_seen: '2026-09-26T00:00:00Z',
    last_review: null,
    next_review_at: null,
    recall_success: 0,
    recall_fail: 0,
    context_exposure: 0,
    possible_issue: false,
    notes: '',
    review_history: [],
    article_exposures: [],
    sources: { fields: [], completeness: { status: 'complete', missing: [], message: '' } },
    ...overrides,
  }
}

function renderDetail(word: WordDetail) {
  return render(
    <MemoryRouter>
      <WordDetailContent word={word} />
    </MemoryRouter>,
  )
}

describe('the word detail page and grouped short meanings', () => {
  it('keeps meanings visible and folds source explanation at the bottom', async () => {
    const { container } = renderDetail(detail({ anchor: '', semantic_note: '' }))
    const toggle = screen.getByText('查看来源说明')
    const fold = toggle.closest('details')!
    expect(fold).not.toHaveAttribute('open')
    expect(container.lastElementChild).toBe(fold)
    expect(screen.getByText('農村的')).toBeVisible()
    expect(screen.queryByText('暂无已确认的核心释义')).not.toBeInTheDocument()
    expect(screen.queryByText('最小语义锚点')).not.toBeInTheDocument()
    expect(screen.getByText('来源记录')).not.toBeVisible()
    await userEvent.click(toggle)
    expect(fold).toHaveAttribute('open')
    expect(screen.getByRole('region', { name: '来源记录' })).toBeVisible()
    expect(screen.getByText('rural adj. 農村的')).toBeInTheDocument()
  })

  it('closes source explanation when another word is selected', async () => {
    const { rerender } = renderDetail(detail())
    await userEvent.click(screen.getByText('查看来源说明'))
    expect(screen.getByText('查看来源说明').closest('details')).toHaveAttribute('open')
    rerender(<MemoryRouter><WordDetailContent word={detail({ lexicon_entry_id: 8, word: 'urban' })} /></MemoryRouter>)
    expect(screen.getByText('查看来源说明').closest('details')).not.toHaveAttribute('open')
  })
  it('shows play’s three verb senses then its noun sense, marking the judged group', () => {
    const { container } = renderDetail(detail({
      word: 'play',
      part_of_speech: 'v.',
      concise_meanings: [
        group({
          pos_key: 'verb', pos_label: '动词', pos_order: 1,
          pos_source: 'reviewer', pos_source_label: '人工试判',
          meanings: [
            meaning({ text: '玩', display_order: 1 }),
            meaning({ text: '演奏', display_order: 2 }),
            meaning({ text: '播放', display_order: 3 }),
          ],
        }),
        group({
          pos_key: 'noun', pos_label: '名词', pos_order: 2,
          meanings: [meaning({ text: '剧', display_order: 1 })],
        }),
      ],
    }))

    expect([...container.querySelectorAll('.concise-pos')].map((n) => n.textContent)).toEqual([
      '动词', '名词',
    ])
    expect(
      [...container.querySelectorAll('.concise-text')].map((n) => n.textContent),
    ).toEqual(['玩', '演奏', '播放', '剧'])
    expect(container.querySelectorAll('.concise-pos-basis')).toHaveLength(1)
    expect(container).toHaveTextContent('词性试判')
    // The source's own single string would be a second claim beside the headings.
    expect(screen.queryByText(/· v\./)).toBeNull()
  })

  it('keeps the part-of-speech chip when nothing is grouped to show instead', () => {
    renderDetail(detail())
    expect(screen.getByText(/· adj\./)).toBeTruthy()
    expect(screen.queryByRole('region', { name: '核心释义' })).toBeNull()
  })

  it('shows the primary position and every additional citation of a value', () => {
    // `performance`: both values come from one zh.wiktionary line and each cites a
    // second source as well, so a reader needs both positions.
    const { container } = renderDetail(detail({
      word: 'performance',
      concise_meanings: [
        group({
          pos_key: 'noun', pos_label: '名词', pos_order: 1,
          meanings: [
            meaning({
              text: '表演',
              display_order: 1,
              source_locator: 'zhwiktionary:8457333:10',
              citations: [
                { citation_order: 1, citation_locator: 'wikdict:37', source_evidence_id: 34 },
                { citation_order: 2, citation_locator: 'zhwiktionary:8457333:11', source_evidence_id: null },
              ],
            }),
            meaning({
              text: '执行',
              display_order: 2,
              source_locator: 'zhwiktionary:8457333:10',
              citations: [
                { citation_order: 1, citation_locator: 'wikdict:37', source_evidence_id: 34 },
              ],
            }),
          ],
        }),
      ],
    }))

    const first = screen.getByText('表演').closest('li')!
    expect(first).toHaveTextContent('来源位置 zhwiktionary:8457333:10')
    expect([...first.querySelectorAll('.concise-citation')].map((n) => n.textContent)).toEqual([
      'wikdict:37', 'zhwiktionary:8457333:11',
    ])

    const second = screen.getByText('执行').closest('li')!
    expect(second).toHaveTextContent('来源位置 zhwiktionary:8457333:10')
    expect([...second.querySelectorAll('.concise-citation')].map((n) => n.textContent)).toEqual([
      'wikdict:37',
    ])
    expect(container.querySelectorAll('.concise-group')).toHaveLength(1)
  })

  it('never presents a supplement as a quotation, even if positions are present', () => {
    renderDetail(detail({
      concise_meanings: [
        group({
          meanings: [
            meaning({
              text: '肥料',
              provenance_kind: 'ai_supplement',
              provenance_label: '自拟补充',
              is_supplement: true,
              is_source_verbatim: false,
              source_locator: 'zhwiktionary:7831922:3',
              citations: [
                { citation_order: 1, citation_locator: 'wikdict:1', source_evidence_id: null },
              ],
              derivation_note: '来源没有中文释义，按词形补充',
            }),
          ],
        }),
      ],
    }))

    expect(screen.getByText('自拟补充')).toBeTruthy()
    expect(screen.getByText('来源没有中文释义，按词形补充')).toBeTruthy()
    expect(screen.queryByText(/来源位置/)).toBeNull()
    expect(screen.queryByText(/附加引用/)).toBeNull()
    expect(screen.queryByText('wikdict:1')).toBeNull()
  })

  it('marks source text unconfirmed when nothing is displayable', () => {
    renderDetail(detail({ concise_meanings: [] }))
    expect(screen.queryByRole('region', { name: '核心释义' })).toBeNull()
    expect(screen.getByText('農村的')).toBeTruthy()
    expect(screen.getByText('词典释义 · 抽取片段，未做全库逐词语义校订')).toBeTruthy()
    expect(screen.queryByText('暂无已确认的核心释义')).toBeNull()
  })

  it('marks uploaded text as user provided and unverified', () => {
    renderDetail(detail({ meaning_origin: 'user_provided', source_meanings: ['用户释义'], source_raw: '' }))
    expect(screen.getByText('用户提供的释义 · 未核实')).toBeTruthy()
    expect(screen.getByText('用户释义')).toBeTruthy()
  })

  it('shows a clear empty state without source or confirmed text', () => {
    renderDetail(detail({ source_meanings: [], source_raw: '' }))
    expect(screen.getByText('暂无可用释义')).toBeTruthy()
    expect(screen.queryByText('词典释义 · 抽取片段，未做全库逐词语义校订')).toBeNull()
  })

  it('does not turn a public import CSV row into a missing dictionary meaning', () => {
    renderDetail(detail({
      word: 'owing to', meaning_origin: 'platform', source_meanings: [],
      source_raw: 'owing to,,netem:rank:5448,70dc6b68',
      sources: { fields: [{ field_kind: 'word', selected: [], candidates: [] }],
        completeness: { status: 'complete', missing: [], message: '' } },
    }))
    expect(screen.getByText('暂无可用释义')).toBeTruthy()
    expect(screen.queryByText('owing to,,netem:rank:5448,70dc6b68')).toBeNull()
    expect(screen.queryByText('词典释义 · 抽取片段，未做全库逐词语义校订')).toBeNull()
  })
})
