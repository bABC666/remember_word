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
  source_semantic_conflict: '固定原文与中文片段不对应',
  source_audit_unresolved: '原文依据不足或存在歧义',
}

export function DictionaryExtractionList({ value }: { value: DictionaryExtraction | null | undefined }) {
  if (!value) return null
  const groups = new Map<string, DictionaryExtractionValue[]>()
  const audited = value.audit_status === 'automated_source_verified'
  const ungrouped: DictionaryExtractionValue[] = []
  const seen = new Set<string>()
  for (const sense of value.senses) {
    const key = `${sense.pos_key ?? ''}\0${sense.text}`
    if (seen.has(key)) continue
    seen.add(key)
    if (audited && !sense.pos_key) {
      ungrouped.push(sense)
      continue
    }
    const label = sense.pos_label || '词性待核'
    groups.set(label, [...(groups.get(label) ?? []), sense])
  }
  if (audited) {
    for (const [label, senses] of groups) {
      groups.set(label, senses.slice().sort((a, b) =>
        Number(a.semantic_scope === 'word_translation') - Number(b.semantic_scope === 'word_translation')))
    }
  }
  const ipa = [...new Set(value.pronunciations.map(item => item.text))]
  const extraGroups = audited ? [...groups].map(([label, senses]) => [label, senses.slice(3)] as const)
    .filter(([, senses]) => senses.length > 0) : []
  const extraUngrouped = audited ? ungrouped.slice(3) : []
  const extraCount = extraGroups.reduce((total, [, senses]) => total + senses.length, extraUngrouped.length)
  return <section className="dictionary-extraction" aria-label="来源义项">
    <span className="source-label">{audited ? '词典释义 · 自动来源核验' : '来源义项 · 自动抽取 · 未核实'}</span>
    {!audited && <p className="muted">按来源词性和义项结构整理，尚未裁定为常用核心释义。</p>}
    {ipa.length > 0 && <p className="phonetic">来源音标：{ipa.map(text => `/${text}/`).join('、')}</p>}
    {[...groups].map(([label, senses]) => <div className="concise-group" key={label}>
      <span className="concise-pos">{label}</span>
      <ol className="concise-meaning-list">{senses.slice(0, audited ? 3 : senses.length).map((sense, i) =>
        <li key={`${sense.locator}-${i}`}>{sense.text}{sense.meaning_kind === 'derived' && <small> · 据英文来源翻译整理</small>}</li>)}</ol>
    </div>)}
    {ungrouped.length > 0 && <ol className="concise-meaning-list">{ungrouped.slice(0, 3).map((sense, i) =>
      <li key={`${sense.locator}-${i}`}>{sense.text}{sense.meaning_kind === 'derived' && <small> · 据英文来源翻译整理</small>}</li>)}</ol>}
    {groups.size === 0 && ungrouped.length === 0 && <p className="muted">{audited
      ? '暂缺可用的词典释义。' : '暂无可可靠分组的来源义项，保留原文待核。'}</p>}
    {extraCount > 0 && <details className="source-raw" key={`extra-${value.entry_id}`}>
      <summary>更多词典释义（{extraCount}）</summary>
      {extraGroups.map(([label, senses]) => <div className="concise-group" key={label}>
        <span className="concise-pos">{label}</span>
        <ol className="concise-meaning-list" start={4}>{senses.map((sense, i) =>
          <li key={`${sense.locator}-${i}`}>{sense.text}{sense.meaning_kind === 'derived' && <small> · 据英文来源翻译整理</small>}</li>)}</ol>
      </div>)}
      {extraUngrouped.length > 0 && <ol className="concise-meaning-list" start={4}>{extraUngrouped.map((sense, i) =>
        <li key={`${sense.locator}-${i}`}>{sense.text}{sense.meaning_kind === 'derived' && <small> · 据英文来源翻译整理</small>}</li>)}</ol>}
    </details>}
    <details className="source-raw dictionary-extraction-evidence" key={`evidence-${value.entry_id}`}>
      <summary>{audited ? '查看词典来源' : `来源与抽取依据（${value.pending_count} 项待核）`}</summary>
      {audited && <p className="muted">已按固定原文自动核对；这不属于人工确认的核心释义。词性不可靠时保留释义并让词性留空。来源待核 {value.pending_count} 项，排除 {value.excluded_count ?? 0} 项。</p>}
      {value.pending_count > 0 && <p className="muted">有歧义的内容未列入上述义项，原文保留供核对。</p>}
      {[...value.senses, ...value.pronunciations, ...(value.pending_values ?? [])].map((item, i) => <div key={`${item.locator}-${i}`}>
        {item.status === 'pending' && <small>待核：{pendingLabels[item.reason ?? ''] ?? '需对照固定原文核查'}</small>}
        {item.status === 'rejected' && <small>已排除：{pendingLabels[item.reason ?? ''] ?? '原文对照不通过'}</small>}
        {item.quality_review?.note && <p className="muted">核验依据：{item.quality_review.note}</p>}
        {item.quality_review?.alignment_note && <p className="muted">{item.quality_review.alignment_note}</p>}
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
