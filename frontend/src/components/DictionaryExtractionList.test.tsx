import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { DictionaryExtractionList } from './DictionaryExtractionList'

it('keeps complete source senses grouped, labelled unconfirmed and evidence collapsed', async () => {
  render(<DictionaryExtractionList value={{ status: 'unconfirmed_source_extraction',
    parser_version: 'netem-rich-v1', pending_count: 2, pending_reasons: ['upstream_error'],
    pronunciations: [], senses: ['玩', '演奏', '扮演', '播放'].map((text, i) => ({
      text, pos_key: 'verb', pos_label: '动词', language: 'en', locator: `wikdict:${i}`,
      status: 'extracted', raw_text: `原文 ${text}`, source: { publisher: 'WikDict',
        license_id: 'CC-BY-SA-4.0', original_file_sha256: 'a'.repeat(64) },
    })).concat([{ text: '剧', pos_key: 'noun', pos_label: '名词', language: 'en',
      locator: 'zhwiktionary:12:4', status: 'extracted', raw_text: '# 剧',
      source: { publisher: 'Wiktionary', license_id: 'CC-BY-SA-4.0', original_file_sha256: 'b'.repeat(64) } }]),
  }} />)
  expect(screen.getByRole('region', { name: '来源义项' })).toHaveTextContent('自动抽取 · 未核实')
  expect(screen.getAllByRole('listitem')).toHaveLength(5)
  expect(screen.getByText('动词')).toBeInTheDocument()
  expect(screen.getByText('名词')).toBeInTheDocument()
  expect(screen.queryByText(/释义裁定/)).not.toBeInTheDocument()
  const toggle = screen.getByText('来源与抽取依据（2 项待核）')
  expect(toggle.closest('details')).not.toHaveAttribute('open')
  await userEvent.click(toggle)
  expect(toggle.closest('details')).toHaveAttribute('open')
  expect(screen.getByText('# 剧')).toBeInTheDocument()
})
