import { useLayoutEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Clock3, TriangleAlert } from 'lucide-react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { ConciseMeaningList } from '../components/ConciseMeaningList'
import { DictionaryExtractionList } from '../components/DictionaryExtractionList'
import { EntrySourceList } from '../components/EntrySourceList'
import { MeaningList } from '../components/MeaningList'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { hasMeaningText } from '../meaningText'
import { dictionaryMeaningNotice } from '../dictionarySource'
import { statusLabels, type LibraryPosition, type WordDetail } from './wordDetailModel'

function exposureTime(value: string | null | undefined) {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString('zh-CN')
}

export function WordDetailContent({ word }: { word: WordDetail }) {
  const rawIsFallback = word.source_meanings.every((meaning) => !meaning.trim())
  // A public import's raw CSV line is provenance, not a definition. Earlier
  // platform entries without source records still use the legacy raw fallback.
  const hasSource = word.meaning_origin === 'platform' && word.sources.fields.length > 0
    ? word.source_meanings.some((meaning) => meaning.trim())
    : hasMeaningText(word.source_meanings, word.source_raw)
  const meaningSources = [...new Set(word.sources.fields
    .filter((field) => field.field_kind === 'meaning')
    .flatMap((field) => field.selected)
    .map((row) => row.source.publisher || row.source.name))]
  const sourceNames = word.sources.fields.filter(field => field.field_kind === 'meaning')
    .flatMap(field => field.selected).map(row => row.source.name)
  // With groups, the headings inside the concise list state the part of speech; the
  // single source-declared `part_of_speech` would be a second claim beside them. With
  // no groups there is nothing else to read and it stays.
  const hasGroups = word.concise_meanings.length > 0
  return (
    <>
      <header>
        <div>
          <h2>{word.word}</h2>
          <p className="phonetic">{word.phonetic || '暂无音标'}{!hasGroups && word.part_of_speech && <> · {word.part_of_speech}</>}</p>
        </div>
        <span className={`status status-${word.status}`}>{word.status === 'new' ? '未学习' : statusLabels[word.status]}</span>
      </header>
      {/* The component supplies its own labelled section, so a word with nothing
          confirmed renders no empty "核心释义" block at all. */}
      <ConciseMeaningList groups={word.concise_meanings} />
      {hasGroups && word.dictionary_extraction ? <details className="source-raw">
        <summary>查看完整来源义项（未核实）</summary>
        <DictionaryExtractionList value={word.dictionary_extraction} />
      </details> : <DictionaryExtractionList value={word.dictionary_extraction} />}
      {!hasGroups && !hasSource && <p className="muted">暂无可用释义</p>}
      {(word.anchor.trim() || word.semantic_note.trim()) && <section>
        {word.anchor.trim() && <><span>最小语义锚点</span><h3>{word.anchor}</h3></>}
        {word.semantic_note && <p>{word.semantic_note}</p>}
      </section>}
      {hasSource && !word.dictionary_extraction && <section aria-label={word.meaning_origin === 'user_provided' ? '用户提供的释义' : sourceNames.some(name => name.startsWith('enwiktionary-translation-')) ? '翻译整理的释义' : '来源原文'}>
        <span>{word.meaning_origin === 'user_provided' ? '用户提供的释义 · 未核实' : dictionaryMeaningNotice(sourceNames)}</span>
        <MeaningList values={word.source_meanings} fallback={word.source_raw} />
      </section>}
      <div className="detail-stats">
        <div><strong>{word.recall_success}</strong><span>成功回忆</span></div>
        <div><strong>{word.recall_fail}</strong><span>回忆失败</span></div>
        <div><strong>{word.context_exposure}</strong><span>阅读暴露</span></div>
      </div>
      <section aria-label="复习历史">
        <span>复习历史</span>
        {word.review_history.length ? (
          <div className="history-list">{word.review_history.map((event) => (
            <div key={event.id}>
              <Clock3 size={15} />
              <span>{new Date(event.timestamp).toLocaleString('zh-CN')}</span>
              <b>{event.result}</b>
              <small>{event.status_before} → {event.status_after} · {event.source}</small>
            </div>
          ))}</div>
        ) : <p className="muted">还没有复习记录。</p>}
      </section>
      <section aria-label="文章暴露">
        <span>文章暴露</span>
        {word.article_exposures.length ? word.article_exposures.map((item) => (
          <blockquote key={item.article_id}>
            {item.context}
            {item.exposure_count != null && <small>出现 {item.exposure_count} 次</small>}
            {exposureTime(item.first_exposed_at) && <small>首次：{exposureTime(item.first_exposed_at)}</small>}
            {exposureTime(item.last_exposed_at) && <small>最近：{exposureTime(item.last_exposed_at)}</small>}
          </blockquote>
        )) : <p className="muted">还没有在阅读文章中出现。</p>}
      </section>
      {word.possible_issue && (
        <div className="detail-warning"><TriangleAlert size={17} />这个词条在导入时被标记为可能有疑点。</div>
      )}
      <details className="word-source-explanation" key={word.lexicon_entry_id}>
        <summary>查看来源说明</summary>
        {word.dictionary_extraction && <section aria-label="原导入释义（未核实）">
          <span>原导入释义 · 未核实</span>
          <MeaningList values={word.source_meanings} />
        </section>}
        {hasSource && word.meaning_origin !== 'user_provided' && <p className="muted">{rawIsFallback
          ? '原始记录行 · 来源未单独标注'
          : `释义采用来源：${meaningSources.join('、') || '未记录（见下方来源记录）'}`}</p>}
        {hasSource && word.source_raw && !rawIsFallback && (
          <details className="source-raw"><summary>{word.meaning_origin === 'user_provided' ? '查看用户提供的原始记录行' : '查看原始记录行（来源可能不同）'}</summary><pre>{word.source_raw}</pre></details>
        )}
        <EntrySourceList sources={word.sources} lexiconId={word.lexicon_id} />
      </details>
    </>
  )
}

export function WordDetailPage({ positions }: { positions: Map<string, LibraryPosition> }) {
  const { wordStateId, entryId } = useParams()
  const location = useLocation()
  useLayoutEffect(() => { window.scrollTo(0, 0) }, [location.key])
  const rawId = entryId ?? wordStateId
  const stateId = rawId && /^[1-9]\d*$/.test(rawId) && Number.isSafeInteger(Number(rawId))
    ? Number(rawId) : null
  const detail = useQuery({
    queryKey: [entryId !== undefined ? 'entry-word' : 'word', stateId],
    queryFn: () => api<WordDetail>(`/api/words/${entryId !== undefined ? 'entry' : 'state'}/${stateId}`),
    enabled: stateId !== null,
    retry: false,
  })
  const from = location.state as { fromListKey?: string; returnTo?: string } | null
  const hasListPosition = Boolean(from?.fromListKey && positions.has(from.fromListKey))
  const returnTo = from?.returnTo && /^\/library(?:\?|$)/.test(from.returnTo) ? from.returnTo : '/library'

  return (
    <div className="page library-detail-page">
      <Link
        className="library-back"
        to={hasListPosition ? returnTo : '/library'}
        state={hasListPosition ? { restoreFromKey: from?.fromListKey } : undefined}
      >
        <ArrowLeft size={18} />返回词库
      </Link>
      {stateId === null || (detail.isError && detail.error instanceof ApiError && detail.error.status === 404) ? (
        <EmptyState title="词条不存在或无法访问" description="请从自己的词库重新选择一个词条。" />
      ) : detail.isPending ? (
        <LoadingState label="正在加载词条详情…" />
      ) : detail.isError ? (
        <ErrorState error={detail.error} action={
          <button className="button secondary" onClick={() => void detail.refetch()}>重试</button>
        } />
      ) : (
        <article className="word-detail"><WordDetailContent word={detail.data} /></article>
      )}
    </div>
  )
}
