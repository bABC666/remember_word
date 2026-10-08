import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { DictionaryExtractionList } from './DictionaryExtractionList'

it('shows source-verified meanings with unknown POS without turning review detail into the lesson', async () => {
  render(<DictionaryExtractionList value={{status: 'unconfirmed_source_extraction',
    parser_version: 'netem-rich-v1', audit_status: 'automated_source_verified',
    pending_count: 0, excluded_count: 1, pending_reasons: [], pronunciations: [],
    senses: [{text: '四月', pos_key: '', pos_label: '', pos_raw: 'pronoun', language: 'en',
      status: 'source_verified', locator: 'wikdict:1', source: {publisher: 'WikDict'}}],
    pending_values: [{text: '错误片段', language: 'en', status: 'rejected', locator: 'wikdict:2',
      reason: 'source_semantic_conflict', quality_review: {note: '固定英文与中文不对应。'},
      source: {publisher: 'WikDict'}}],
  }} />)
  const region = screen.getByRole('region', {name: '来源义项'})
  expect(region).toHaveTextContent('四月')
  expect(screen.queryByText('代词')).not.toBeInTheDocument()
  expect(screen.queryByText('词性待核')).not.toBeInTheDocument()
  expect(screen.queryByText(/暂无可可靠分组/)).not.toBeInTheDocument()
  expect(region).toHaveTextContent('词典释义 · 自动来源核验')
  const evidence = screen.getByText('查看词典来源').closest('details')
  expect(evidence).not.toHaveAttribute('open')
  await userEvent.click(screen.getByText('查看词典来源'))
  expect(evidence).toHaveAttribute('open')
  expect(evidence).toHaveTextContent('已排除')
  expect(evidence).toHaveTextContent('固定英文与中文不对应。')
})

it('shows up to three source values per POS and preserves further values in a fold', async () => {
  const senses = ['动词一', '动词二', '动词三', '动词四', '名词一', '名词二'].map((text, i) => ({
    text, language: 'en', status: 'source_verified', locator: `test:${i}`,
    pos_key: i < 4 ? 'verb' : 'noun', pos_label: i < 4 ? '动词' : '名词', source: {publisher: '词典'},
  }))
  render(<DictionaryExtractionList value={{parser_version: 'netem-rich-v1', status: 'unconfirmed_source_extraction',
    audit_status: 'automated_source_verified', pending_count: 0, pending_reasons: [], pronunciations: [], senses}} />)
  expect(screen.getByText('动词三')).toBeVisible()
  expect(screen.getByText('名词一')).toBeVisible()
  expect(screen.getByText('名词二')).toBeVisible()
  expect(screen.getByText('动词四')).not.toBeVisible()
  await userEvent.click(screen.getByText('更多词典释义（1）'))
  expect(screen.getByText('动词四')).toBeVisible()
})

it('closes review evidence when a different entry is selected in the same pane', async () => {
  const value = {entry_id: 1, parser_version: 'netem-rich-v1', status: 'unconfirmed_source_extraction' as const,
    audit_status: 'automated_source_verified' as const, pending_count: 0, pending_reasons: [],
    pronunciations: [], senses: [{text: '四月', language: 'en', status: 'source_verified',
      locator: 'source:1', source: {publisher: '词典'}}]}
  const {rerender} = render(<DictionaryExtractionList value={value} />)
  await userEvent.click(screen.getByText('查看词典来源'))
  expect(screen.getByText('查看词典来源').closest('details')).toHaveAttribute('open')
  rerender(<DictionaryExtractionList value={{...value, entry_id: 2}} />)
  expect(screen.getByText('查看词典来源').closest('details')).not.toHaveAttribute('open')
})

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
it('keeps third-party source and original licence links in collapsed evidence', async () => {
  render(<DictionaryExtractionList value={{parser_version:'netem-rich-v1',status:'unconfirmed_source_extraction',
    audit_status:'automated_source_verified',pending_count:0,pending_reasons:[],pronunciations:[],
    senses:[{text:'高声',language:'en',status:'source_verified',locator:'zh:1:2',source:{publisher:'维基词典',
      attribution:{creators:'维基与 CC-CEDICT 贡献者',modifications:'保留原 3.0；改编 4.0',
        license_url:'https://creativecommons.org/licenses/by-sa/4.0/',
        links:[{label:'CC-CEDICT 原料来源',url:'https://cc-cedict.org/wiki/'}]}}}]}} />)
  const link=screen.getByRole('link',{name:'CC-CEDICT 原料来源',hidden:true})
  expect(link).not.toBeVisible()
  await userEvent.click(screen.getByText('查看词典来源'))
  expect(link).toBeVisible()
  expect(link).toHaveAttribute('href','https://cc-cedict.org/wiki/')
})
