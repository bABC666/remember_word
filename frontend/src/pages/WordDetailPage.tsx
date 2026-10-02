import { useLayoutEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Clock3, TriangleAlert } from 'lucide-react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { ConciseMeaningList } from '../components/ConciseMeaningList'
import { EntrySourceList } from '../components/EntrySourceList'
import { MeaningList } from '../components/MeaningList'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { statusLabels, type LibraryPosition, type WordDetail } from './wordDetailModel'

function exposureTime(value: string | null | undefined) {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString('zh-CN')
}

export function WordDetailContent({ word }: { word: WordDetail }) {
  const rawIsFallback = word.source_meanings.every((meaning) => !meaning.trim())
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
        <span className={`status status-${word.status}`}>{statusLabels[word.status]}</span>
      </header>
      {/* The component supplies its own labelled section, so a word with nothing
          confirmed renders no empty "核心释义" block at all. */}
      <ConciseMeaningList groups={word.concise_meanings} />
      <section>
        <span>最小语义锚点</span>
        <h3>{word.anchor || '—'}</h3>
        {word.semantic_note && <p>{word.semantic_note}</p>}
      </section>
      <section aria-label={word.meaning_origin === 'user_provided' ? '用户提供的释义' : '原书完整释义'}>
        <span>{word.meaning_origin === 'user_provided' ? '用户提供的释义 · 未核实' : '原书完整释义'}</span>
        <MeaningList values={word.source_meanings} fallback={word.source_raw} />
        {word.source_raw && !rawIsFallback && (
          <details className="source-raw"><summary>查看原书原文</summary><pre>{word.source_raw}</pre></details>
        )}
      </section>
      {/* Placed after the source's own text, so a reader who doubts a value meets the
          record of where it came from next. The concise blocks above keep their own
          labels: a rewritten or self-authored short value is never presented as the
          source's words, and this block does not change that. The lexicon is passed in
          because the entry's own lexicon is what makes the card anchor unambiguous. */}
      <EntrySourceList sources={word.sources} lexiconId={word.lexicon_id} />
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
    </>
  )
}

export function WordDetailPage({ positions }: { positions: Map<string, LibraryPosition> }) {
  const { wordStateId } = useParams()
  const location = useLocation()
  useLayoutEffect(() => { window.scrollTo(0, 0) }, [location.key])
  const stateId = wordStateId && /^[1-9]\d*$/.test(wordStateId) && Number.isSafeInteger(Number(wordStateId))
    ? Number(wordStateId) : null
  const detail = useQuery({
    queryKey: ['word', stateId],
    queryFn: () => api<WordDetail>(`/api/words/state/${stateId}`),
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
