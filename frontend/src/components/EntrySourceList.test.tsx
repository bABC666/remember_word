import { readFileSync } from 'node:fs'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { EntrySources, SourceEvidence } from '../types'
import { EntrySourceList } from './EntrySourceList'

/**
 * One evidence row, as `GET /api/words/state/{id}` reports it.
 *
 * `selected_for_default` is what decides whether a row may stand beside a displayed
 * value; `source_revision_url` is empty whenever the server could not build an honest
 * link, which is a state the page has to show rather than paper over.
 */
function evidence(overrides: Partial<SourceEvidence> = {}): SourceEvidence {
  return {
    source_evidence_id: 1,
    field_kind: 'meaning',
    row_locator: 2,
    sense_key: 'meaning@2',
    raw_text: '補充來源釋義',
    decision: 'selected',
    selected_for_default: true,
    selection_order: 0,
    source_revision: '2222222',
    source_revision_url: 'https://example.test/rev/2222222',
    source: {
      source_artifact_id: 4,
      role: 'meaning',
      name: 'supplement.csv',
      publisher: '词表发布方乙',
      version: '2026-09-25',
      license_id: 'CC-BY-SA-4.0',
    },
    ...overrides,
  }
}

function sources(overrides: Partial<EntrySources> = {}): EntrySources {
  return {
    fields: [{
      field_kind: 'meaning',
      selected: [evidence()],
      candidates: [],
    }],
    completeness: { status: 'complete', missing: [], message: '' },
    ...overrides,
  }
}

function renderList(value: EntrySources, lexiconId = 12) {
  return render(
    <MemoryRouter>
      <EntrySourceList sources={value} lexiconId={lexiconId} />
    </MemoryRouter>,
  )
}

/** The adopted rows of one field, found by the label the component gives them. */
function adopted(field: string) {
  return within(screen.getByRole('list', { name: `${field}：已采用的来源` }))
}

function candidates(field: string) {
  return within(screen.getByRole('list', { name: `${field}：未采用的候选` }))
}

describe('EntrySourceList', () => {
  it('shows an adopted row among the adopted and never among the candidates', () => {
    renderList(sources())

    const list = adopted('释义')
    expect(list.getByText('補充來源釋義')).toBeInTheDocument()
    expect(list.getByText(/词表发布方乙/)).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: '释义：未采用的候选' })).not.toBeInTheDocument()
  })

  it('distinguishes an original wikitext line from an old cache definition index', () => {
    renderList(sources({ fields: [{ field_kind: 'meaning', selected: [
      evidence({ source_evidence_id: 11, row_locator: 2, sense_key: 'wikitext:line:9|15' }),
      evidence({ source_evidence_id: 12, row_locator: 3, sense_key: 'cache:def:0|2' }),
    ], candidates: [] }] }))
    expect(screen.getByText(/导入文件第 2 行 · 原 wikitext 行号 9\|15/)).toBeInTheDocument()
    expect(screen.getByText(/导入文件第 3 行 · 旧清洗缓存定义序号 0\|2（非原文行号）/)).toBeInTheDocument()
  })

  it('keeps a recorded but unadopted row out of the adopted list', () => {
    // The import wrote a row for the primary's meaning too and a human took the
    // supplement's instead. Both stay visible; only one may sit beside the value.
    renderList(sources({
      fields: [{
        field_kind: 'meaning',
        selected: [evidence({ raw_text: '补充来源释义', source: {
          source_artifact_id: 4, role: 'meaning', name: 'supplement.csv',
          publisher: '词表发布方乙', version: '2026-09-25', license_id: 'CC-BY-SA-4.0',
        } })],
        candidates: [evidence({
          source_evidence_id: 2, raw_text: '主词表释义', decision: 'not_selected',
          selected_for_default: false, selection_order: null, source_revision: '1111111',
          source_revision_url: 'https://example.test/rev/1111111',
          source: {
            source_artifact_id: 3, role: 'primary', name: 'primary.csv',
            publisher: '词表发布方甲', version: '2026-09-25', license_id: 'CC-BY-SA-4.0',
          },
        })],
      }],
    }))

    expect(adopted('释义').getByText('补充来源释义')).toBeInTheDocument()
    expect(adopted('释义').queryByText('主词表释义')).not.toBeInTheDocument()
    const other = candidates('释义')
    expect(other.getByText('主词表释义')).toBeInTheDocument()
    expect(other.getByText('未采用')).toBeInTheDocument()
  })

  it('reports each field with the source that field actually came from', () => {
    renderList(sources({
      fields: [
        {
          field_kind: 'word',
          selected: [evidence({
            field_kind: 'word', raw_text: 'Amber', source_revision: '1111111',
            source_revision_url: 'https://example.test/rev/1111111',
            source: {
              source_artifact_id: 3, role: 'primary', name: 'primary.csv',
              publisher: '词表发布方甲', version: '2026-09-25', license_id: 'CC-BY-SA-4.0',
            },
          })],
          candidates: [],
        },
        {
          field_kind: 'phonetic',
          selected: [evidence({
            field_kind: 'phonetic', raw_text: '/ˈæmbər/', source_revision: '2222222',
            source_revision_url: 'https://example.test/rev/2222222',
          })],
          candidates: [],
        },
      ],
    }))

    expect(adopted('单词').getByText(/词表发布方甲/)).toBeInTheDocument()
    expect(adopted('单词').getByText('Amber')).toBeInTheDocument()
    // A different field, a different source: the primary's publisher must not appear
    // as the origin of the phonetic, nor the supplement's as the word's.
    expect(adopted('音标').getByText(/词表发布方乙/)).toBeInTheDocument()
    expect(adopted('音标').getByText('/ˈæmbər/')).toBeInTheDocument()
    expect(adopted('音标').queryByText(/词表发布方甲/)).not.toBeInTheDocument()
    expect(adopted('单词').queryByText(/词表发布方乙/)).not.toBeInTheDocument()
  })

  it('states an empty source record in words instead of omitting the block', () => {
    renderList(sources({
      fields: [],
      completeness: {
        status: 'incomplete',
        missing: [{ code: 'no_evidence', field_kind: '', message: '该词条没有任何来源证据行' }],
        message: '该词条的来源记录不完整：该词条没有任何来源证据行。',
      },
    }))

    // The display design is explicit that this block must not silently disappear.
    const region = screen.getByRole('region', { name: '来源记录' })
    expect(within(region).getByText(/该词条的来源记录不完整/)).toBeInTheDocument()
    expect(within(region).queryAllByRole('list')).toHaveLength(0)
  })

  it('offers no revision link when the server sent an empty url', () => {
    renderList(sources({
      fields: [{
        field_kind: 'word',
        // A recorded revision with no link: the frozen mapping declares no usable
        // template, so the honest answer is to say so and build nothing.
        selected: [evidence({
          field_kind: 'word', raw_text: 'Amber', source_revision: '1111111',
          source_revision_url: '',
        })],
        candidates: [],
      }],
      completeness: {
        status: 'incomplete',
        missing: [{
          code: 'source_revision_url_unavailable', field_kind: 'word',
          message: '字段「word」的采用来源无法合成固定修订链接',
        }],
        message: '该词条的来源记录不完整：字段「word」的采用来源无法合成固定修订链接。',
      },
    }))

    const list = adopted('单词')
    expect(list.queryByRole('link', { name: /修订/ })).not.toBeInTheDocument()
    expect(list.getByText(/1111111/)).toBeInTheDocument()
    expect(list.getByText(/无法合成固定修订链接/)).toBeInTheDocument()
  })

  it('distinguishes a missing revision from an unavailable link', () => {
    renderList(sources({
      fields: [{
        field_kind: 'word',
        selected: [evidence({ field_kind: 'word', raw_text: 'Amber', source_revision: '', source_revision_url: '' })],
        candidates: [],
      }],
      completeness: {
        status: 'incomplete',
        missing: [{
          code: 'source_revision_missing', field_kind: 'word',
          message: '字段「word」的采用来源没有记录固定修订号',
        }],
        message: '该词条的来源记录不完整：字段「word」的采用来源没有记录固定修订号。',
      },
    }))

    const list = adopted('单词')
    expect(list.getByText(/没有记录固定修订号/)).toBeInTheDocument()
    expect(list.queryByRole('link', { name: /修订/ })).not.toBeInTheDocument()
    // "No revision" and "cannot link" are different facts and must not read alike.
    expect(list.queryByText(/无法合成/)).not.toBeInTheDocument()
  })

  it('links the revision only when the server sent a url for it', () => {
    renderList(sources())

    expect(adopted('释义').getByRole('link', { name: /固定修订/ })).toHaveAttribute(
      'href', 'https://example.test/rev/2222222',
    )
  })

  it('links every row to its own card in this word\'s lexicon', () => {
    renderList(sources({
      fields: [{
        field_kind: 'meaning',
        selected: [evidence({ source_evidence_id: 9 })],
        candidates: [evidence({
          source_evidence_id: 10, decision: 'not_selected', selected_for_default: false,
          source_revision_url: '', source_revision: '',
          source: {
            source_artifact_id: 7, role: 'primary', name: 'primary.csv',
            publisher: '词表发布方甲', version: '2026-09-25', license_id: '',
          },
        })],
      }],
    }), 12)

    // The anchor carries the lexicon as well as the artifact, so an artifact used by
    // two lexicons resolves to this word's own card rather than to the first match --
    // and every row gets one, a candidate included: it is a source we recorded.
    expect(adopted('释义').getByRole('link', { name: '来源详情' })).toHaveAttribute(
      'href', '/sources#source-12-4',
    )
    expect(candidates('释义').getByRole('link', { name: '来源详情' })).toHaveAttribute(
      'href', '/sources#source-12-7',
    )
  })

  it('takes the lexicon from the entry, not from a constant', () => {
    // The same payload under another lexicon: the link follows the entry's own lexicon,
    // which is the only id the sources page can be holding a card under.
    renderList(sources(), 11)

    expect(adopted('释义').getByRole('link', { name: '来源详情' })).toHaveAttribute(
      'href', '/sources#source-11-4',
    )
  })

  it('never writes a recorded declaration up as a grant', () => {
    renderList(sources())
    const region = screen.getByRole('region', { name: '来源记录' })
    const text = region.textContent ?? ''

    for (const claim of ['已获授权', '符合 CC 要求', '官方授权']) {
      expect(text, claim).not.toContain(claim)
    }
    expect(text).toContain('不代表授权已获确认')
    // The recorded identifier itself is shown, so safety is not achieved by hiding it.
    expect(text).toContain('CC-BY-SA-4.0')
  })

  it('names a field it was not told about rather than dropping it', () => {
    renderList(sources({
      fields: [{ field_kind: 'sense_note', selected: [evidence({ field_kind: 'sense_note' })], candidates: [] }],
    }))

    expect(screen.getByRole('list', { name: 'sense_note：已采用的来源' })).toBeInTheDocument()
  })
})

/**
 * The field label is an `h3`, and so is every heading in the detail it sits in: the
 * pre-existing `.word-detail section h3 { font: 600 24px Georgia, serif }` matched it too
 * and, out-ranking the label's own rule, rendered it as a 24px serif heading. jsdom
 * computes no cascade from the real sheet here, so the two properties that decide the
 * outcome -- what the rule declares, and that it out-ranks the one it competes with --
 * are asserted against the file. The measured proof is the browser walkthrough.
 */
describe('the field label rule', () => {
  // Comments are stripped first: they sit between rules and would otherwise be read as
  // part of the next selector.
  const styles = readFileSync('src/styles.css', 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '')

  /** The declaration bodies of every rule whose selector list contains `selector`. */
  const body = (selector: string) =>
    [...styles.matchAll(/([^{}]*)\{([^{}]*)\}/g)]
      .filter((match) => match[1].split(',').map((entry) => entry.trim().replace(/\s+/g, ' ')).includes(selector))
      .map((match) => match[2])
      .join(';')

  /** [ids, classes and attributes, types] -- enough to order two such selectors. */
  const specificity = (selector: string): [number, number, number] => [
    (selector.match(/#[\w-]+/g) ?? []).length,
    (selector.match(/\.[\w-]+|\[[^\]]*\]/g) ?? []).length,
    (selector.match(/(?:^|[\s>+~])[a-z][\w-]*/gi) ?? []).length,
  ]
  const outranks = (a: number[], b: number[]) =>
    a[0] !== b[0] ? a[0] > b[0] : a[1] !== b[1] ? a[1] > b[1] : a[2] > b[2]

  it('declares the intended 13px bold interface label', () => {
    const declared = body('.entry-sources .field-source-name')
    expect(declared).toContain('font-size: 13px')
    expect(declared).toContain('font-weight: 700')
    // The `font:` shorthand it competes with also sets a serif family, so the label
    // takes the interface font back explicitly.
    expect(declared).toContain('font-family: inherit')
  })

  it('out-ranks the detail heading rule that was overriding it', () => {
    expect(body('.entry-sources .field-source-name'), 'the label rule must exist').not.toBe('')
    const label = specificity('.entry-sources .field-source-name')
    const heading = specificity('.word-detail section h3')
    expect(outranks(label, heading), `${label} must out-rank ${heading}`).toBe(true)
    // ...and that rule is untouched, so the headings it is meant for are unaffected.
    expect(body('.word-detail section h3')).toContain('font: 600 24px Georgia, serif')
  })

  it('is scoped to the block, so no other heading is reached', () => {
    // Nothing declares the label's font on a bare `.field-source-name`; the only bare
    // `h3` rule in the sheet is the `margin-top: 0` reset every heading already had.
    expect(body('.field-source-name')).toBe('')
    expect(body('h3')).not.toMatch(/font-size|font-family|font-weight|font:/)
  })
})
