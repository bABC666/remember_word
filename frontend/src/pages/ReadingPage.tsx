import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookMarked, CheckCircle2, Sparkles } from 'lucide-react'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import type { Article, ReviewResult } from '../types'

function ReadingQuiz({ article }: { article: Article }) {
  const [index, setIndex] = useState(0)
  const [meaning, setMeaning] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [suggestion, setSuggestion] = useState<{ suggestion: string; explanation: string } | null>(null)
  const words = article.quiz_words ?? []
  const word = words[index]
  const review = useMutation({
    mutationFn: (result: ReviewResult) => api(`/api/study/words/${word.id}/review`, { method: 'POST', body: JSON.stringify({ result, source: 'reading', review_type: 'context_recall', article_id: article.id }) }),
    onSuccess: () => { setIndex((value) => value + 1); setMeaning(''); setRevealed(false); setSuggestion(null) },
  })
  const judge = useMutation({
    mutationFn: () => api<{ suggestion: string; explanation: string }>(`/api/articles/${article.id}/judge`, { method: 'POST', body: JSON.stringify({ word_id: word.id, user_meaning: meaning }) }),
    onSuccess: setSuggestion,
  })
  if (!word) return <EmptyState title="阅读后测试已完成" description="这篇文章中实际出现的目标词都已经复习过了。" />
  return (
    <section className="reading-quiz">
      <div className="quiz-progress">阅读后测试 · {index + 1} / {words.length}</div>
      <h2>{word.word}</h2><p className="context-quote">“{word.context}”</p>
      <label>写下你在当前语境中理解的意思<textarea value={meaning} onChange={(event) => setMeaning(event.target.value)} placeholder="先写下自己的理解，再核对答案…" /></label>
      {!revealed ? <button className="button primary" disabled={!meaning.trim()} onClick={() => setRevealed(true)}>核对答案</button> : <div className="quiz-answer"><span>最小语义锚点</span><strong>{word.anchor}</strong><span>原书完整释义</span><p>{word.source_meanings.join('；') || word.source_raw}</p><div className="quiz-tools"><button className="button secondary" disabled={judge.isPending} onClick={() => judge.mutate()}><Sparkles size={16} />{judge.isPending ? '正在分析…' : '获取 AI 建议（可选）'}</button>{suggestion && <p><b>建议：{suggestion.suggestion}</b> · {suggestion.explanation}</p>}{judge.isError && <p className="muted">AI 暂时不可用，不影响你自行选择。</p>}</div><div className="quiz-rate"><button onClick={() => review.mutate('fail')}>不会</button><button onClick={() => review.mutate('fuzzy')}>模糊</button><button onClick={() => review.mutate('know')}>会</button></div></div>}
    </section>
  )
}

export function ReadingPage() {
  const client = useQueryClient()
  const [currentId, setCurrentId] = useState<number | null>(null)
  const list = useQuery({ queryKey: ['articles'], queryFn: () => api<Article[]>('/api/articles') })
  const articleId = currentId ?? list.data?.[0]?.id
  const detail = useQuery({ queryKey: ['article', articleId], queryFn: () => api<Article>(`/api/articles/${articleId}`), enabled: Boolean(articleId) })
  const generate = useMutation({ mutationFn: () => api<Article>('/api/articles/generate', { method: 'POST', body: JSON.stringify({ target_count: 20 }) }), onSuccess: async (article) => { setCurrentId(article.id); await client.invalidateQueries({ queryKey: ['articles'] }) } })
  const complete = useMutation({ mutationFn: () => api<Article>(`/api/articles/${articleId}/complete`, { method: 'POST' }), onSuccess: (article) => client.setQueryData(['article', articleId], article) })
  if (list.isLoading) return <LoadingState label="正在查看阅读记录…" />
  if (list.isError) return <ErrorState error={list.error} />
  const article = detail.data
  return (
    <div className="page reading-page">
      <header className="page-head split"><div><h1>阅读练习</h1><p>让单词在真实语境里再次出现，自然度永远比“硬塞单词”重要。</p></div><button className="button primary" disabled={generate.isPending} onClick={() => generate.mutate()}><Sparkles size={17} />{generate.isPending ? '正在生成…' : '生成今日阅读'}</button></header>
      {generate.isError && <ErrorState error={generate.error} />}
      {!articleId && <EmptyState title="今天还没有阅读文章" description="先从词库中选择一组需要巩固的词，生成一篇 500–800 词的英文文章。" action={<button className="button primary" onClick={() => generate.mutate()}>生成今日阅读</button>} />}
      {articleId && detail.isLoading && <LoadingState label="正在打开文章…" />}
      {detail.isError && <ErrorState error={detail.error} />}
      {article && !article.completed && <article className="article-sheet"><header><BookMarked size={22} /><div><h2>{article.title}</h2><time>{new Date(article.created_at).toLocaleDateString('zh-CN')}</time></div></header><div className="article-content">{article.content.split(/\n\n+/).map((paragraph) => <p key={paragraph.slice(0, 30)}>{paragraph}</p>)}</div><footer><p>读完后，只测试正文中真实出现的目标词。</p><button className="button primary" disabled={complete.isPending} onClick={() => complete.mutate()}>{complete.isPending ? '正在保存阅读记录…' : '完成阅读'}</button></footer></article>}
      {article?.completed && <><div className="completed-banner"><CheckCircle2 size={20} />阅读已完成，下面开始语境词义测试。</div><ReadingQuiz article={article} /></>}
    </div>
  )
}
