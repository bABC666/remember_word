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
import { useAuth } from '../useAuth'
import type { ReviewResult, Word } from '../types'

type StudyWord = Word & { relearning_today?: boolean }
type StudyQueue = { total: number; words: StudyWord[]; attempted_today?: number; day?: string; daily_new_words?: { target: number; consumed_today: number; remaining: number } }

export function StudyPage() {
  const location = useLocation()
  const selectedLexicon = new URLSearchParams(location.search).get('lexicon_id')
  const lexiconQuery = selectedLexicon && /^\d+$/.test(selectedLexicon) ? `?lexicon_id=${selectedLexicon}` : ''
  return <StudySession key={lexiconQuery} lexiconQuery={lexiconQuery} />
}

function StudySession({ lexiconQuery }: { lexiconQuery: string }) {
  const { user } = useAuth()
  const client = useQueryClient()
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10))
  const [revealed, setRevealed] = useState(false)
  const queryKey = ['study-today', user?.id, lexiconQuery, day]
  const query = useQuery({ queryKey, queryFn: () => api<StudyQueue>(`/api/study/today${lexiconQuery}`), staleTime: 0, refetchOnWindowFocus: false })
  const words = query.data?.words ?? []
  const current = words[0]
  const attempted = query.data?.attempted_today ?? 0
  const total = attempted + words.filter((word) => !word.relearning_today).length
  const position = Math.min(attempted + 1, total)
  const groups = current?.concise_meanings ?? []
  const dictionarySources = current?.source_meaning_sources ?? []
  const hasDictionaryMeaning = dictionarySources.length > 0 && !!current?.source_meanings.some((value) => value.trim())
  const hasSource = current ? (current.source_meaning_sources !== undefined
    ? current.source_meanings.some((value) => value.trim())
    : hasMeaningText(current.source_meanings, current.source_raw)) : false
  const mutation = useMutation({
    // `word_state_id`, not the legacy id: a word added from an article has no
    // `word` row, and the legacy route deliberately does not accept a state id.
    mutationFn: ({ result, word }: { result: ReviewResult; word: StudyWord }) => api(`/api/study/word-states/${word.word_state_id}/review`, { method: 'POST', body: JSON.stringify({ result, source: 'daily', review_type: 'recall' }) }),
    onMutate: async () => { await client.cancelQueries({ queryKey }) },
    onSuccess: async (_, { result, word: reviewed }) => {
      // Advance the cached queue, not a component-local index. This callback also
      // runs if the user navigates away while the answer is being saved.
      client.setQueryData<StudyQueue>(queryKey, (queue) => {
        if (!queue) return queue
        const remaining = queue.words.filter((word) => word.word_state_id !== reviewed.word_state_id)
        if (result !== 'know') remaining.push({ ...reviewed, relearning_today: true })
        return { ...queue, words: remaining, total: remaining.length,
          attempted_today: (queue.attempted_today ?? 0) + (reviewed.relearning_today ? 0 : 1) }
      })
      setRevealed(false)
      void client.invalidateQueries({ queryKey: ['dashboard'] })
      void client.invalidateQueries({ queryKey: ['words'] })
      // Reconcile with persisted history, including another tab's answers and
      // any next page of due/new words. Inactive routes simply become stale.
      await client.invalidateQueries({ queryKey })
    },
  })
  const grade = (result: ReviewResult) => mutation.mutate({ result, word: current })

  useEffect(() => {
    const timer = window.setInterval(() => setDay(new Date().toISOString().slice(0, 10)), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => { setRevealed(false) }, [current?.word_state_id, day, lexiconQuery])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!current || mutation.isPending || (event.target as HTMLElement).closest('button, a, summary, input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      if (event.code === 'Space') { event.preventDefault(); setRevealed(true) }
      if (revealed && event.key === '1') mutation.mutate({ result: 'fail', word: current })
      if (revealed && event.key === '2') mutation.mutate({ result: 'fuzzy', word: current })
      if (revealed && event.key === '3') mutation.mutate({ result: 'know', word: current })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, mutation, revealed])

  if (query.isLoading) return <LoadingState label="正在准备学习队列…" />
  if (query.isError && !query.data) return <ErrorState error={query.error} />
  if (!current) return <EmptyState title="今天暂时没有待学习的单词" description={query.data?.daily_new_words?.remaining === 0
    ? `今日新词目标为 ${query.data.daily_new_words.target} 个，已学 ${query.data.daily_new_words.consumed_today} 个。想继续学新词，可以在设置中提高每日新词目标并保存。`
    : attempted ? '今天的学习和强化复习已经完成。' : '先选择词库或导入一些单词。'} action={<>
      <Link className="button secondary" to="/lexicons">选择词库</Link>
      <Link className="button secondary" to="/settings">调整每日新词目标</Link>
      <button className="button secondary" onClick={() => void query.refetch()}>重新检查学习队列</button>
    </>} />

  return (
    <div className="study-page">
      <header className="study-head"><h1>今日学习</h1><Link to="/lexicons">切换词库</Link><div><span>{position} / {total}</span><div className="progress"><i style={{ width: `${total ? (position / total) * 100 : 0}%` }} /></div></div></header>
      {current.relearning_today && <p className="study-practice-note" role="status">强化复习 · 把今天没记牢的词再练一遍，选“会”后当天不再出现。</p>}
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
            <section className="study-anchor" aria-label="语义锚点">
              <span>最小语义锚点</span><p>{current.anchor || '尚未生成语义锚点'}</p>
              {current.semantic_note && <p className="semantic-note">{current.semantic_note}</p>}
            </section>
          </div>
        )}
      </section>
      <footer className="study-actions">
        {mutation.isError && <div className="inline-error">{mutation.error.message}</div>}
        <span>{revealed ? '选择你的真实回忆情况' : '空格显示答案'}</span>
        <div>
          <button disabled={!revealed || mutation.isPending} className="rate fail" onClick={() => grade('fail')}><b>1</b> 不会</button>
          <button disabled={!revealed || mutation.isPending} className="rate fuzzy" onClick={() => grade('fuzzy')}><b>2</b> 模糊</button>
          <button disabled={!revealed || mutation.isPending} className="rate know" onClick={() => grade('know')}><b>3</b> 会</button>
        </div>
        <button className="text-button" onClick={() => setRevealed(false)}><RotateCcw size={15} />重新回忆</button>
      </footer>
    </div>
  )
}
