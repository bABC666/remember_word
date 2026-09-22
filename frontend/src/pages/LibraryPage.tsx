import { useDeferredValue, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Clock3, Search, TriangleAlert } from 'lucide-react'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { MeaningList } from '../components/MeaningList'
import type { Word } from '../types'

const statusLabels: Record<string, string> = { new: '新词', familiar: '眼熟', learning: '学习中', known: '已知', weak: '薄弱', mastered: '已掌握' }

export function LibraryPage() {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [view, setView] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const deferredSearch = useDeferredValue(search)
  const list = useQuery({ queryKey: ['words', deferredSearch, status, view], queryFn: () => api<{ total: number; words: Word[] }>(`/api/words?search=${encodeURIComponent(deferredSearch)}&status=${status}&view=${view}`) })
  const detail = useQuery({ queryKey: ['word', selected], queryFn: () => api<Word & { review_history: Array<Record<string, string>>; article_exposures: Array<Record<string, string>> }>(`/api/words/${selected}`), enabled: Boolean(selected) })
  return (
    <div className="page library-page">
      <header className="page-head"><h1>我的词库</h1><p>查看每个词的原始资料、学习状态与完整历史。</p></header>
      <div className="library-toolbar"><label><Search size={18} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索单词、释义或 anchor" /></label><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{Object.entries(statusLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select><div className="view-tabs"><button className={view === 'weak' ? 'active' : ''} onClick={() => setView(view === 'weak' ? '' : 'weak')}>薄弱词</button><button className={view === 'recent' ? 'active' : ''} onClick={() => setView(view === 'recent' ? '' : 'recent')}>最近加入</button><button className={view === 'stale' ? 'active' : ''} onClick={() => setView(view === 'stale' ? '' : 'stale')}>长期未复习</button></div></div>
      {list.isLoading && <LoadingState />}{list.isError && <ErrorState error={list.error} />}
      {list.data && list.data.words.length === 0 && <EmptyState title="没有找到单词" description={search || status || view ? '换一个搜索条件试试。' : '从单词书导入后，词条会出现在这里。'} />}
      {list.data && list.data.words.length > 0 && <div className="library-layout"><div className="word-list"><div className="word-list-head"><span>{list.data.total} 个词条</span><span>状态 / 复习</span></div>{list.data.words.map((word) => <button className={selected === word.id ? 'selected' : ''} key={word.id} onClick={() => setSelected(word.id)}><div><strong>{word.word}</strong><small className="phonetic">{word.phonetic} · {word.part_of_speech}</small></div><div><span className={`status status-${word.status}`}>{statusLabels[word.status]}</span><small>{word.last_review ? new Date(word.last_review).toLocaleDateString('zh-CN') : '尚未复习'}</small></div></button>)}</div><aside className="word-detail">{!selected && <EmptyState title="选择一个单词" description="右侧会显示原书信息、学习数据和全部历史。" />}{detail.isLoading && <LoadingState />}{detail.isError && <ErrorState error={detail.error} />}{detail.data && <><header><div><h2>{detail.data.word}</h2><p className="phonetic">{detail.data.phonetic} · {detail.data.part_of_speech}</p></div><span className={`status status-${detail.data.status}`}>{statusLabels[detail.data.status]}</span></header><section><span>最小语义锚点</span><h3>{detail.data.anchor || '—'}</h3>{detail.data.semantic_note && <p>{detail.data.semantic_note}</p>}</section><section><span>原书完整信息</span><MeaningList values={detail.data.source_meanings} fallback={detail.data.source_raw} /><pre>{detail.data.source_raw}</pre></section><div className="detail-stats"><div><strong>{detail.data.recall_success}</strong><span>成功回忆</span></div><div><strong>{detail.data.recall_fail}</strong><span>回忆失败</span></div><div><strong>{detail.data.context_exposure}</strong><span>阅读暴露</span></div></div><section><span>复习历史</span>{detail.data.review_history.length ? <div className="history-list">{detail.data.review_history.map((event) => <div key={event.id}><Clock3 size={15} /><span>{new Date(event.timestamp).toLocaleString('zh-CN')}</span><b>{event.result}</b><small>{event.status_before} → {event.status_after} · {event.source}</small></div>)}</div> : <p className="muted">还没有复习记录。</p>}</section><section><span>文章暴露</span>{detail.data.article_exposures.length ? detail.data.article_exposures.map((item) => <blockquote key={item.article_id}>{item.context}</blockquote>) : <p className="muted">还没有在阅读文章中出现。</p>}</section>{detail.data.possible_issue && <div className="detail-warning"><TriangleAlert size={17} />这个词条在导入时被标记为可能有疑点。</div>}</>}</aside></div>}
    </div>
  )
}
