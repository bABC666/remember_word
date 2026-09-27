import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ConciseMeaningList } from './ConciseMeaningList'
import type { ConciseMeaning } from '../types'

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
    derivation_note: '',
    confirmed_by: 'owner',
    confirmed_at: '2026-09-26T00:00:00Z',
    ...overrides,
  }
}

describe('ConciseMeaningList', () => {
  it('renders the confirmed values in display order', () => {
    render(<ConciseMeaningList values={[
      meaning({ text: '海拔', display_order: 2 }),
      meaning({ text: '高度', display_order: 1 }),
    ]} />)
    const items = screen.getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('高度'),
      expect.stringContaining('海拔'),
    ])
  })

  it('does not label a value the source itself contains', () => {
    render(<ConciseMeaningList values={[meaning()]} />)
    expect(screen.queryByText('来源原文')).toBeNull()
    expect(screen.getByText('已由 owner 人工确认')).toBeTruthy()
  })

  it('labels a converted value and shows what was changed', () => {
    render(<ConciseMeaningList values={[meaning({
      text: '农村的', provenance_kind: 'derived', provenance_label: '据来源改写',
      is_source_verbatim: false, derivation_note: '来源为「農村的」，此处为简体转换',
    })]} />)
    expect(screen.getByText('据来源改写')).toBeTruthy()
    expect(screen.getByText('来源为「農村的」，此处为简体转换')).toBeTruthy()
  })

  it('marks a self-authored supplement so it cannot read as a quotation', () => {
    render(<ConciseMeaningList values={[meaning({
      text: '看似', provenance_kind: 'ai_supplement', provenance_label: '自拟补充',
      is_source_verbatim: false, source_locator: '',
      derivation_note: '现有来源只有较少见义项，补充常见义',
    })]} />)
    expect(screen.getByText('自拟补充')).toBeTruthy()
    // A supplement has no source position, and the component must not invent one.
    expect(screen.queryByText(/来源位置/)).toBeNull()
    const item = screen.getAllByRole('listitem')[0]
    expect(within(item).getByText('看似')).toBeTruthy()
  })

  it('renders nothing when there is no confirmed value', () => {
    const { container } = render(<ConciseMeaningList values={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('names every confirmer, not just the first slot’s', () => {
    render(<ConciseMeaningList values={[
      meaning({ text: '高度', display_order: 1, confirmed_by: 'first-admin' }),
      meaning({ text: '海拔', display_order: 2, confirmed_by: 'second-admin' }),
    ]} />)
    // Attributing the second value to the first slot's approver would credit someone
    // who never saw it.
    expect(screen.getByText('已由 first-admin、second-admin 人工确认')).toBeTruthy()
  })

  it('does not print a source position for a supplement even if one is present', () => {
    // The server cannot produce this shape (a CHECK constraint forbids it); the
    // component must not depend on that to stay honest.
    render(<ConciseMeaningList values={[meaning({
      text: '看似', provenance_kind: 'ai_supplement', provenance_label: '自拟补充',
      is_source_verbatim: false, is_supplement: true, source_locator: 'primary:9',
      derivation_note: '补充常见义',
    })]} />)
    expect(screen.getByText('自拟补充')).toBeTruthy()
    expect(screen.queryByText(/来源位置/)).toBeNull()
  })
})
