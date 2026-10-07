import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, RotateCcw } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { ConciseMeaningList } from '../components/ConciseMeaningList'
import { DictionaryExtractionList } from '../components/DictionaryExtractionList'
import { MeaningList } from '../components/MeaningList'
import { DictionarySourceNote } from '../components/DictionarySourceNote'
import { hasMeaningText } from '../meaningText'
import type { ReviewResult, Word } from '../types'

export function StudyPage() {
  const location = useLocation()
  const selectedLexicon = new URLSearchParams(location.search).get('lexicon_id')
  const lexiconQuery = selectedLexicon && /^\d+$/.test(selectedLexicon) ? `?lexicon_id=${selectedLexicon}` : ''
  // A new scope owns a fresh recall session, including progress and mutations.
  return <StudySession key={lexiconQuery} lexiconQuery={lexiconQuery} />
}

function StudySession({ lexiconQuery }: { lexiconQuery: string }) {
  const client = useQueryClient()
  const [reviewedIds, setReviewedIds] = useState<Set<number>>(() => new Set())
  const [revealed, setRevealed] = useState(false)
  const query = useQuery({ queryKey: ['study-today', lexiconQuery], queryFn: () => api<{ total: number; words: Word[]; daily_new_words?: { target: number; consumed_today: number; remaining: number } }>(`/api/study/today${lexiconQuery}`) })
  const words = query.data?.words ?? []
  const index = words.findIndex((word) => !reviewedIds.has(word.word_state_id))
  const current = words[index]
  const groups = current?.concise_meanings ?? []
  const dictionarySources = current?.source_meaning_sources ?? []
  const hasDictionaryMeaning = dictionarySources.length > 0 && !!current?.source_meanings.some((value) => value.trim())
  const hasSource = current ? (current.source_meaning_sources !== undefined
    ? current.source_meanings.some((value) => value.trim())
    : hasMeaningText(current.source_meanings, current.source_raw)) : false
  const mutation = useMutation({
    // `word_state_id`, not the legacy id: a word added from an article has no
    // `word` row, and the legacy route deliberately does not accept a state id.
    mutationFn: async (result: ReviewResult) => {
      const id = current.word_state_id
      await api(`/api/study/word-states/${id}/review`, { method: 'POST', body: JSON.stringify({ result, source: 'daily', review_type: 'recall' }) })
      return id
    },
    onSuccess: async (id) => {
      setReviewedIds((previous) => new Set([...previous, id]))
      setRevealed(false)
      void client.invalidateQueries({ queryKey: ['dashboard'] })
      void client.invalidateQueries({ queryKey: ['words'] })
      await client.invalidateQueries({ queryKey: ['study-today'], refetchType: 'none' })
      if (words.every((word) => word.word_state_id === id || reviewedIds.has(word.word_state_id))) {
        await query.refetch()
      }
    },
  })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!current || mutation.isPending || (event.target as HTMLElement).closest('button, a, summary, input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      if (event.code === 'Space') { event.preventDefault(); setRevealed(true) }
      if (revealed && event.key === '1') mutation.mutate('fail')
      if (revealed && event.key === '2') mutation.mutate('fuzzy')
      if (revealed && event.key === '3') mutation.mutate('know')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, mutation, revealed])

  if (query.isLoading || (!current && query.isFetching)) return <LoadingState label="正在准备学习队列…" />
  if (query.isError) return <ErrorState error={query.error} />
  if (!current) return <EmptyState title="今天暂时没有待学习的单词" description={query.data?.daily_new_words?.remaining === 0
    ? `今日新词目标为 ${query.data.daily_new_words.target} 个，已学 ${query.data.daily_new_words.consumed_today} 个。想继续学新词，可以在设置中提高每日新词目标并保存。`
    : words.length ? '这一轮已经完成，做得很好。' : '先选择词库或导入一些单词。'} action={<>
      <Link className="button secondary" to="/lexicons">选择词库</Link>
      <Link className="button secondary" to="/settings">调整每日新词目标</Link>
      <button className="button secondary" onClick={() => void query.refetch()}>重新检查学习队列</button>
    </>} />

  return (
    <div className="study-page">
      <header className="study-head"><h1>今日学习</h1><Link to="/lexicons">切换词库</Link><div><span>{index + 1} / {words.length}</span><div className="progress"><i style={{ width: `${((index + 1) / words.length) * 100}%` }} /></div></div></header>
      <section className="study-stage">
        {/* The part-of-speech chip comes from `lexicon_entry.part_of_speech`, a single
            source-declared string. When the confirmed values carry their own groups,
            those headings are the authoritative statement and this chip would be a
            second, possibly different one -- so it is suppressed rather than shown
            twice. With no groups there is nothing else to read, and the chip stays. */}
        <div className="word-main"><h2>{current.word}</h2><p className="phonetic">{current.phonetic}</p>{groups.length === 0 && current.part_of_speech && <span>{current.part_of_speech}</span>}</div>
        {!revealed ? (
          <button className="reveal-button" onClick={() => setRevealed(true)}><BookOpen size={19} />显示答案 <kbd>Space</kbd></button>
        ) : (
          <div className="answer-area">
            {groups.length > 0 ? <ConciseMeaningList groups={groups} /> : !hasDictionaryMeaning && !current.dictionary_extraction && (
              <p className="muted">{hasSource ? '暂无已确认的核心释义' : '暂无可用释义'}</p>
            )}
            {groups.length === 0 && <DictionaryExtractionList value={current.dictionary_extraction} />}
            {groups.length > 0 && current.dictionary_extraction && <details className="source-raw">
              <summary>查看完整来源义项（未核实）</summary>
              <DictionaryExtractionList value={current.dictionary_extraction} />
            </details>}
            {hasDictionaryMeaning && groups.length === 0 && !current.dictionary_extraction && (
              <section aria-label="词典释义" className="dictionary-meaning">
                <DictionarySourceNote key={current.word_state_id} word={current} />
                <MeaningList values={current.source_meanings} />
              </section>
            )}
            <div className="answer-divider" />
            <span>最小语义锚点</span><h3>{current.anchor || '尚未生成语义锚点'}</h3>
            {hasSource && hasDictionaryMeaning && groups.length === 0 && !current.dictionary_extraction ? null : hasSource && (current.meaning_origin === 'user_provided' ? (
              <section aria-label="用户提供的释义">
                <span className="source-label">用户提供的释义 · 未核实</span>
                <MeaningList values={current.source_meanings} fallback={current.source_raw} />
              </section>
            ) : (
              <details className="source-raw study-source">
                <summary>查看来源原文（未确认）</summary>
                <span className="source-label">来源原文 · 未确认短义</span>
                <p className="muted"><Link to={`/library/${current.word_state_id}`}>查看词条来源记录</Link>（可能未记录）</p>
                <MeaningList values={current.source_meanings} fallback={current.source_raw} />
                {current.source_raw && <pre className="source-raw-line">{current.source_raw}</pre>}
              </details>
            ))}
            {current.semantic_note && <p className="semantic-note">{current.semantic_note}</p>}
          </div>
        )}
      </section>
      <footer className="study-actions">
        {mutation.isError && <div className="inline-error">{mutation.error.message}</div>}
        <span>{revealed ? '选择你的真实回忆情况' : '空格显示答案'}</span>
        <div>
          <button disabled={!revealed || mutation.isPending} className="rate fail" onClick={() => mutation.mutate('fail')}><b>1</b> 不会</button>
          <button disabled={!revealed || mutation.isPending} className="rate fuzzy" onClick={() => mutation.mutate('fuzzy')}><b>2</b> 模糊</button>
          <button disabled={!revealed || mutation.isPending} className="rate know" onClick={() => mutation.mutate('know')}><b>3</b> 会</button>
        </div>
        <button className="text-button" onClick={() => setRevealed(false)}><RotateCcw size={15} />重新回忆</button>
      </footer>
    </div>
  )
}
