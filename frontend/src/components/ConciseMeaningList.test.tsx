import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ConciseMeaningList } from './ConciseMeaningList'
import type { ConciseMeaning, ConciseMeaningGroup } from '../types'

function meaning(overrides: Partial<ConciseMeaning> = {}): ConciseMeaning {
  return {
    text: '敬畏',
    display_order: 1,
    provenance_kind: 'source',
    provenance_label: '来源原文',
    is_source_verbatim: true,
    is_supplement: false,
    source_locator: 'zhwiktionary:4',
    source_evidence_id: null,
    citations: [],
    derivation_note: '',
    confirmed_by: 'owner',
    confirmed_at: '2026-09-26T00:00:00Z',
    ...overrides,
  }
}

function group(overrides: Partial<ConciseMeaningGroup> = {}): ConciseMeaningGroup {
  return {
    pos_key: 'noun',
    pos_label: '名词',
    pos_source: 'pos_section',
    pos_source_label: '来源小节标题',
    pos_order: 1,
    meanings: [meaning()],
    ...overrides,
  }
}

/** A value that was rewritten from the source, which is the ordinary case. */
function derived(text: string, displayOrder: number): ConciseMeaning {
  return meaning({
    text,
    display_order: displayOrder,
    provenance_kind: 'derived',
    provenance_label: '据来源改写',
    is_source_verbatim: false,
    source_locator: `zhwiktionary:7993707:${displayOrder + 8}`,
    derivation_note: '由来源行抽义',
  })
}

describe('ConciseMeaningList', () => {
  it('renders the groups in pos_order and each group’s values in display_order', () => {
    // `play`: three verb senses and one noun sense. Groups are handed over in the wrong
    // order and so are the verb values, so only the component's own sorting can produce
    // the expected result.
    const { container } = render(
      <ConciseMeaningList
        groups={[
          group({
            pos_key: 'noun',
            pos_label: '名词',
            pos_order: 2,
            meanings: [derived('剧', 1)],
          }),
          group({
            pos_key: 'verb',
            pos_label: '动词',
            pos_order: 1,
            meanings: [derived('播放', 3), derived('玩', 1), derived('演奏', 2)],
          }),
        ]}
      />,
    )

    expect(
      [...container.querySelectorAll('.concise-pos')].map((node) => node.textContent),
    ).toEqual(['动词', '名词'])

    const groups = container.querySelectorAll('.concise-group')
    expect(groups).toHaveLength(2)
    expect(
      [...within(groups[0] as HTMLElement).getAllByRole('listitem')].map(
        (item) => item.querySelector('.concise-text')?.textContent,
      ),
    ).toEqual(['玩', '演奏', '播放'])
    expect(
      [...within(groups[1] as HTMLElement).getAllByRole('listitem')].map(
        (item) => item.querySelector('.concise-text')?.textContent,
      ),
    ).toEqual(['剧'])
  })

  it('marks a group whose part of speech a person judged, and not a sourced one', () => {
    const { container } = render(
      <ConciseMeaningList
        groups={[
          group({
            pos_key: 'noun',
            pos_label: '名词',
            pos_source: 'reviewer',
            pos_source_label: '裁定者试判',
            pos_order: 1,
            meanings: [derived('表演', 1)],
          }),
          group({
            pos_key: 'verb',
            pos_label: '动词',
            pos_source: 'pos_section',
            pos_source_label: '来源小节标题',
            pos_order: 2,
            meanings: [derived('演奏', 1)],
          }),
        ]}
      />,
    )

    // The score of a heading a person judged is worth saying; a sourced one needs no
    // marker, and marking both would make the marker meaningless.
    const bases = [...container.querySelectorAll('.concise-pos-basis')]
    expect(bases).toHaveLength(1)
    expect(bases[0].textContent).toBe('词性试判')
    // The server's own wording stays reachable rather than being re-derived here.
    expect(bases[0].getAttribute('title')).toBe('裁定者试判')
  })

  it('shows the primary position and every additional citation, in citation order', () => {
    // `performance`: both values come from one zh.wiktionary line and each also cites a
    // second source, so a reader needs both positions to check the value.
    render(
      <ConciseMeaningList
        groups={[
          group({
            pos_key: 'noun',
            pos_order: 1,
            meanings: [
              derived('表演', 1),
              meaning({
                text: '执行',
                display_order: 2,
                provenance_kind: 'derived',
                provenance_label: '据来源改写',
                is_source_verbatim: false,
                source_locator: 'zhwiktionary:8457333:10',
                citations: [
                  { citation_order: 2, citation_locator: 'zhwiktionary:8457333:11', source_evidence_id: null },
                  { citation_order: 1, citation_locator: 'wikdict:37', source_evidence_id: 34 },
                ],
              }),
            ],
          }),
        ]}
      />,
    )

    const item = screen.getByText('执行').closest('li')!
    expect(within(item).getByText('来源位置 zhwiktionary:8457333:10')).toBeTruthy()
    expect(
      [...item.querySelectorAll('.concise-citation')].map((node) => node.textContent),
    ).toEqual(['wikdict:37', 'zhwiktionary:8457333:11'])
  })

  it('does not label a value the source itself contains', () => {
    render(<ConciseMeaningList groups={[group()]} />)
    expect(screen.queryByText('来源原文')).toBeNull()
    expect(screen.getByText('释义裁定：owner')).toBeTruthy()
  })

  it('labels a converted value and shows what was changed', () => {
    render(
      <ConciseMeaningList
        groups={[
          group({
            meanings: [
              derived('农村的', 1),
              meaning({ derivation_note: '来源为「農村的」，此处为简体转换' }),
            ],
          }),
        ]}
      />,
    )
    expect(screen.getByText('据来源改写')).toBeTruthy()
    expect(screen.getByText('来源为「農村的」，此处为简体转换')).toBeTruthy()
  })

  it('marks a self-authored supplement so it cannot read as a quotation', () => {
    render(
      <ConciseMeaningList
        groups={[
          group({
            meanings: [
              meaning({
                text: '看似',
                provenance_kind: 'ai_supplement',
                provenance_label: '自拟补充',
                is_source_verbatim: false,
                is_supplement: true,
                source_locator: '',
                derivation_note: '现有来源只有较少见义项，补充常见义',
              }),
            ],
          }),
        ]}
      />,
    )
    expect(screen.getByText('自拟补充')).toBeTruthy()
    // A supplement has no source position, and the component must not invent one.
    expect(screen.queryByText(/来源位置/)).toBeNull()
    expect(screen.queryByText(/附加引用/)).toBeNull()
    const item = screen.getAllByRole('listitem')[0]
    expect(within(item).getByText('看似')).toBeTruthy()
  })

  it('does not print any source position for a supplement even if the payload carries one', () => {
    // The server cannot produce this shape (a CHECK constraint forbids the primary
    // pointer and the service forbids citations); the component must not depend on that
    // to stay honest.
    render(
      <ConciseMeaningList
        groups={[
          group({
            meanings: [
              meaning({
                text: '看似',
                provenance_kind: 'ai_supplement',
                provenance_label: '自拟补充',
                is_source_verbatim: false,
                is_supplement: true,
                source_locator: 'primary:9',
                citations: [
                  { citation_order: 1, citation_locator: 'wikdict:1', source_evidence_id: 7 },
                ],
                derivation_note: '补充常见义',
              }),
            ],
          }),
        ]}
      />,
    )
    expect(screen.getByText('自拟补充')).toBeTruthy()
    expect(screen.queryByText(/来源位置/)).toBeNull()
    expect(screen.queryByText(/附加引用/)).toBeNull()
    expect(screen.queryByText('wikdict:1')).toBeNull()
  })

  it('renders nothing when there is no group', () => {
    const { container } = render(<ConciseMeaningList groups={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('names every confirmer, across groups as well as within one', () => {
    render(
      <ConciseMeaningList
        groups={[
          group({
            pos_key: 'verb',
            pos_label: '动词',
            pos_order: 1,
            meanings: [derived('玩', 1), meaning({ text: '演奏', display_order: 2, confirmed_by: 'second-admin' })],
          }),
          group({
            pos_key: 'noun',
            pos_label: '名词',
            pos_order: 2,
            meanings: [meaning({ text: '剧', confirmed_by: 'third-admin' })],
          }),
        ]}
      />,
    )
    // Attributing a value to another value's approver would credit someone who never
    // saw it.
    expect(
      screen.getByText('释义裁定：owner、second-admin、third-admin'),
    ).toBeTruthy()
  })
})
