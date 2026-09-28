import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { BookOpen, RotateCcw } from 'lucide-react'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { ConciseMeaningList } from '../components/ConciseMeaningList'
import { MeaningList } from '../components/MeaningList'
import type { ReviewResult, Word } from '../types'

export function StudyPage() {
  const [index, setIndex] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const query = useQuery({ queryKey: ['study-today'], queryFn: () => api<{ total: number; words: Word[] }>('/api/study/today') })
  const words = query.data?.words ?? []
  const current = words[index]
  // A word with nothing displayable answers with an empty list, which is the fallback
  // signal -- never a server-side substitute of something unreviewed.
  const groups = current?.concise_meanings ?? []
  const mutation = useMutation({
    // `word_state_id`, not the legacy id: a word added from an article has no
    // `word` row, and the legacy route deliberately does not accept a state id.
    mutationFn: (result: ReviewResult) => api(`/api/study/word-states/${current.word_state_id}/review`, { method: 'POST', body: JSON.stringify({ result, source: 'daily', review_type: 'recall' }) }),
    onSuccess: () => { setIndex((value) => value + 1); setRevealed(false) },
  })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!current || mutation.isPending || ['INPUT', 'TEXTAREA'].includes((event.target as HTMLElement).tagName)) return
      if (event.code === 'Space') { event.preventDefault(); setRevealed(true) }
      if (revealed && event.key === '1') mutation.mutate('fail')
      if (revealed && event.key === '2') mutation.mutate('fuzzy')
      if (revealed && event.key === '3') mutation.mutate('know')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, mutation, revealed])

  if (query.isLoading) return <LoadingState label="正在准备学习队列…" />
  if (query.isError) return <ErrorState error={query.error} />
  if (!current) return <EmptyState title="今天暂时没有待学习的单词" description={words.length ? '这一轮已经完成，做得很好。' : '先导入一些单词，或者稍后回来复习。'} action={<a className="button secondary" href="/import">去导入单词</a>} />

  return (
    <div className="study-page">
      <header className="study-head"><h1>今日学习</h1><div><span>{index + 1} / {words.length}</span><div className="progress"><i style={{ width: `${((index + 1) / words.length) * 100}%` }} /></div></div></header>
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
            {/* The owner's rule for this round: prefer one to three short, common
                senses **per part of speech**. They are shown only when a person
                confirmed them; otherwise the answer falls back to exactly what it
                showed before. */}
            {groups.length > 0 ? (
              <>
                <ConciseMeaningList groups={groups} />
                <div className="answer-divider" />
                <span>最小语义锚点</span><h3>{current.anchor || '尚未生成语义锚点'}</h3>
                <details className="source-raw study-source">
                  <summary>查看原书完整释义</summary>
                  <span className="source-label">原书完整释义</span>
                  <MeaningList values={current.source_meanings} fallback={current.source_raw} />
                  {current.source_raw && (
                    <pre className="source-raw-line">{current.source_raw}</pre>
                  )}
                </details>
              </>
            ) : (
              <>
                <span>最小语义锚点</span><h3>{current.anchor || '尚未生成语义锚点'}</h3>
                <div className="answer-divider" />
                <span className="source-label">原书完整释义</span>
                <MeaningList values={current.source_meanings} fallback={current.source_raw} />
              </>
            )}
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
