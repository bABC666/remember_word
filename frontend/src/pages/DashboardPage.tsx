import { useQuery } from '@tanstack/react-query'
import { ArrowRight, CheckCircle2, Circle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ErrorState, LoadingState } from '../components/States'

interface Dashboard { today_new: number; due_reviews: number; weak_words: number; reading_status: string; streak_days: number }

export function DashboardPage() {
  const query = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Dashboard>('/api/dashboard') })
  if (query.isLoading) return <LoadingState label="正在整理今天的学习计划…" />
  if (query.isError) return <ErrorState error={query.error} />
  const data = query.data!
  const readingDone = data.reading_status === 'completed'
  return (
    <div className="page dashboard-page">
      <header className="hero-head">
        <p className="day-label">{new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())}</p>
        <h1>今天也从一个词开始。</h1>
        <p>坚持一点点，词汇会带你去更远的地方。</p>
        <Link className="button primary hero-button" to="/study">开始今日学习 <ArrowRight size={18} /></Link>
      </header>
      <section className="stats-row" aria-label="今日统计">
        <div><span>今日新词</span><strong>{data.today_new}</strong><small>新的单词，新的可能</small></div>
        <div><span>待复习</span><strong className="green">{data.due_reviews}</strong><small>温故而知新</small></div>
        <div><span>薄弱词</span><strong className="amber">{data.weak_words}</strong><small>再多一点练习</small></div>
        <div><span>连续学习</span><strong>{data.streak_days}<em> 天</em></strong><small>好的习惯，会有回报</small></div>
      </section>
      <section className="reading-strip">
        <div><h2>今日阅读 <span className={readingDone ? 'reading-done' : ''}>{readingDone ? '● 已完成' : '● 尚未完成'}</span></h2><p>每天一篇英文短文，提升语感，积累表达。</p></div>
        <Link className="button secondary" to="/reading">{data.reading_status === 'not_generated' ? '生成今日阅读' : '进入今日阅读'}</Link>
      </section>
      <section className="next-section">
        <h2>接下来</h2><p>按照计划，循序渐进，让每一天都有收获。</p>
        <div className="timeline">
          <div><Circle size={18} className="filled" /><div><strong>学习今日新词</strong><small>预计 15 分钟 · {data.today_new} 个单词</small></div><Link to="/study">下一步</Link></div>
          <div><Circle size={18} /><div><strong>复习待复习单词</strong><small>预计 20 分钟 · {data.due_reviews} 个单词</small></div><span>巩固记忆</span></div>
          <div><CheckCircle2 size={18} className={readingDone ? 'done' : ''} /><div><strong>完成今日阅读</strong><small>预计 15 分钟 · 一篇短文</small></div><span>在语境中理解</span></div>
        </div>
      </section>
    </div>
  )
}
