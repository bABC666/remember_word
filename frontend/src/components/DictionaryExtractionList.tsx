import type { DictionaryExtraction, DictionaryExtractionValue } from '../types'

const pendingLabels: Record<string, string> = {
  translation_quality_not_reviewed: '尚未完成逐项英中原文对照',
  shared_translation_multiple_glosses: '译文共用多个英文义项，逐义对应不明确',
  prior_source_quality_hold_requires_review: '已有来源质量疑点，需重新核查',
  upstream_translation_conflict_over_sour: '固定原文中的英中释义不一致',
  upstream_proper_name_pronoun_requires_review: '原文词性标记与专名用法存在冲突',
  missing_or_multiple_pos: '原文未能明确唯一词性',
  missing_pos_heading: '缺少明确词性标题',
  unexpanded_template: '释义包含尚未解释的模板或标记',
  bare_line_requires_review: '未编号的文本需确认是否属于释义',
  unlabelled_variant_requires_review: '发音变体没有明确的英语口音标注',
  ipa_format_requires_review: '音标格式需对照原文核查',
  license_marker_requires_review: '原文包含需要单独核查的许可标记',
}

export function DictionaryExtractionList({ value }: { value: DictionaryExtraction | null | undefined }) {
  if (!value) return null
  const groups = new Map<string, DictionaryExtractionValue[]>()
  for (const sense of value.senses) {
    const label = sense.pos_label || '词性待核'
    groups.set(label, [...(groups.get(label) ?? []), sense])
  }
  const ipa = [...new Set(value.pronunciations.map(item => item.text))]
  return <section className="dictionary-extraction" aria-label="来源义项">
    <span className="source-label">来源义项 · 自动抽取 · 未核实</span>
    <p className="muted">按来源词性和义项结构整理，尚未裁定为常用核心释义。</p>
    {ipa.length > 0 && <p className="phonetic">来源音标：{ipa.map(text => `/${text}/`).join('、')}</p>}
    {[...groups].map(([label, senses]) => <div className="concise-group" key={label}>
      <span className="concise-pos">{label}</span>
      <ol className="concise-meaning-list">{senses.map((sense, i) =>
        <li key={`${sense.locator}-${i}`}>{sense.text}</li>)}</ol>
    </div>)}
    {groups.size === 0 && <p className="muted">暂无可可靠分组的来源义项，保留原文待核。</p>}
    <details className="source-raw dictionary-extraction-evidence">
      <summary>来源与抽取依据（{value.pending_count} 项待核）</summary>
      {value.pending_count > 0 && <p className="muted">有歧义的内容未列入上述义项，原文保留供核对。</p>}
      {[...value.senses, ...value.pronunciations, ...(value.pending_values ?? [])].map((item, i) => <div key={`${item.locator}-${i}`}>
        {item.status === 'pending' && <small>待核：{pendingLabels[item.reason ?? ''] ?? '需对照固定原文核查'}</small>}
        <small>{item.source.publisher} · {item.source.license_id} · {item.source.version}</small>
        {item.source.attribution && <div>
          <p>{item.source.attribution.creators}</p>
          <p className="muted">既有来源处理说明：{item.source.attribution.modifications}</p>
          <p className="muted">{item.source.attribution.disclaimer}</p>
          <a href={item.source.attribution.license_url} target="_blank" rel="noreferrer">改编许可</a>
          {item.source.attribution.source_url && <> · <a href={item.source.attribution.source_url} target="_blank" rel="noreferrer">来源</a></>}
        </div>}
        {item.source.extraction_modifications && <p className="muted">{item.source.extraction_modifications}</p>}
        <p className="muted">{item.locator}</p>
        {item.pos_raw && <small>来源词性：{item.pos_raw} · 词性位置：{item.pos_locator}</small>}
        <small className="extraction-hash">文件 SHA-256：{item.source.original_file_sha256 || '见来源记录'}</small>
        {item.raw_text && <pre className="source-raw-line">{item.raw_text}</pre>}
      </div>)}
      {(value.originals ?? []).map((original, i) => <details className="source-raw" key={`${original.source.body_sha256}-${i}`}>
        <summary>完整固定原文 · {original.source.publisher} · {original.source.revision}</summary>
        <small className="extraction-hash">文件 SHA-256：{original.source.original_file_sha256}</small>
        <pre className="source-raw-line">{original.raw_text}</pre>
      </details>)}
    </details>
  </section>
}
