import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { SourceAttribution } from './SourceAttribution'
import { sourceCardHref } from '../sourceAnchor'
import { dictionaryMeaningNotice, dictionarySourceName } from '../dictionarySource'
import type { Word } from '../types'

export function DictionarySourceNote({ word }: { word: Word }) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLElement>(null)
  const sources = word.source_meaning_sources ?? []
  useEffect(() => {
    if (!open) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const opener = trigger.current
    return () => { document.body.style.overflow = previousOverflow; opener?.focus() }
  }, [open])
  return <>
    <p className="dictionary-source-note">{sources.some(s => s.name?.startsWith('enwiktionary-translation-')) ? 'AI译释' : '词典释义'} · {[...new Set(sources.map(s => dictionarySourceName(s.name)))].join('、')} · {' '}
      <button ref={trigger} type="button" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(true)}>来源与许可</button>
    </p>
    {open && createPortal(<div className="modal-overlay" onClick={() => setOpen(false)}>
      <section ref={dialog} className="modal-dialog dictionary-source-dialog" role="dialog" aria-modal="true" aria-label="来源与许可"
        onClick={e => e.stopPropagation()} onKeyDown={e => {
          e.stopPropagation()
          if (e.key === 'Escape') { e.preventDefault(); setOpen(false) }
          if (e.key === 'Tab') {
            const items = [...(dialog.current?.querySelectorAll<HTMLElement>('button, a[href]') ?? [])]
            const first = items[0], last = items[items.length - 1]
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
            if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
          }
        }}>
        <header><h2>来源与许可</h2><button className="text-button" onClick={() => setOpen(false)}>关闭</button></header>
        <p className="muted">{sources.some(s => s.name?.startsWith('enwiktionary-'))
          ? dictionaryMeaningNotice(sources.map(s => s.name))
          : '词典释义为抽取片段，未做全库逐词语义校订。'}</p>
        {sources.map((source, index) => <section key={index}>
          <h3>{dictionarySourceName(source.name)}</h3>
          <p>导入版本：{source.version}</p>
          <p className="source-attribution">原文位置：{source.source_position}；导入 CSV 行：{source.import_csv_line}</p>
          {source.file_sha256 && <p className="source-attribution">导入文件 SHA-256：<code>{source.file_sha256}</code></p>}
          {source.source_revision_url && <p><a href={source.source_revision_url} target="_blank" rel="noreferrer noopener">固定词条修订 {source.source_revision}</a></p>}
          <SourceAttribution value={source.attribution} historyUrl={source.source_history_url} />
          <Link to={source.source_artifact_id ? sourceCardHref(word.lexicon_id, source.source_artifact_id) : `/library/${word.word_state_id}`}
            onClick={() => setOpen(false)}>{dictionarySourceName(source.name)} 来源详情</Link>
        </section>)}
        <p><Link to={`/library/${word.word_state_id}`} onClick={() => setOpen(false)}>当前词条来源记录与原文位置</Link></p>
      </section>
    </div>, document.body)}
  </>
}
