import { useDeferredValue, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import type { Word } from '../types'
import { WordDetailContent } from './WordDetailPage'
import { statusLabels, type LibraryPosition, type WordDetail } from './wordDetailModel'

const MOBILE_QUERY = '(max-width: 900px)'
const views = ['weak', 'recent', 'stale'] as const

function useMobileLayout() {
  const [mobile, setMobile] = useState(() => window.matchMedia?.(MOBILE_QUERY).matches ?? false)
  useEffect(() => {
    const query = window.matchMedia?.(MOBILE_QUERY)
    if (!query) return
    const onChange = (event: MediaQueryListEvent) => setMobile(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return mobile
}

function WordRowContent({ word }: { word: Word }) {
  return (
    <>
      <div>
        <strong>{word.word}</strong>
        <small className="phonetic">{word.phonetic} · {word.part_of_speech}</small>
      </div>
      <div>
        <span className={`status status-${word.status}`}>{statusLabels[word.status]}</span>
        <small>{word.last_review ? new Date(word.last_review).toLocaleDateString('zh-CN') : '尚未复习'}</small>
      </div>
    </>
  )
}

export function LibraryPage({ positions }: { positions: Map<string, LibraryPosition> }) {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const mobile = useMobileLayout()
  const [selected, setSelected] = useState<number | null>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const search = params.get('search') ?? ''
  const rawStatus = params.get('status') ?? ''
  const status = Object.hasOwn(statusLabels, rawStatus) ? rawStatus : ''
  const rawView = params.get('view') ?? ''
  const view = views.some((item) => item === rawView) ? rawView : ''
  const deferredSearch = useDeferredValue(search)
  const list = useQuery({
    queryKey: ['words', deferredSearch, status, view],
    queryFn: () => api<{ total: number; words: Word[] }>(
      `/api/words?search=${encodeURIComponent(deferredSearch)}&status=${status}&view=${view}`
    ),
  })
  const detail = useQuery({
    queryKey: ['word', selected],
    queryFn: () => api<WordDetail>(`/api/words/state/${selected}`),
    enabled: !mobile && selected !== null,
  })

  const updateParam = (name: 'search' | 'status' | 'view', value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(name, value)
    else next.delete(name)
    setParams(next, { replace: true, state: location.state })
  }

  const rememberPosition = (wordStateId: number) => {
    positions.set(location.key, {
      listTop: listRef.current?.scrollTop ?? 0,
      pageY: window.scrollY,
      focusedId: wordStateId,
    })
  }

  useLayoutEffect(() => {
    const restoreFromKey = (location.state as { restoreFromKey?: string } | null)?.restoreFromKey
    const positionKey = restoreFromKey && positions.has(restoreFromKey) ? restoreFromKey : location.key
    const previous = positions.get(positionKey)
    if (!previous || !list.data || !listRef.current) return
    listRef.current.scrollTop = previous.listTop
    window.scrollTo(0, previous.pageY)
    const row = [...listRef.current.querySelectorAll<HTMLAnchorElement>('a[data-word-state-id]')]
      .find((item) => item.dataset.wordStateId === String(previous.focusedId))
    if (row) row.focus({ preventScroll: true })
    else titleRef.current?.focus({ preventScroll: true })
    // Keep the source entry while its detail route remains in browser history.
  }, [list.data, location.key, location.state, positions])

  return (
    <div className="page library-page">
      <header className="page-head">
        <h1 ref={titleRef} tabIndex={-1}>我的词库</h1>
        <p>查看每个词的原始资料、学习状态与完整历史。</p>
      </header>
      <div className="library-toolbar">
        <label>
          <Search size={18} />
          <input aria-label="搜索单词" value={search}
            onChange={(event) => updateParam('search', event.target.value)}
            placeholder="搜索单词、释义或 anchor" />
        </label>
        <select aria-label="学习状态" value={status}
          onChange={(event) => updateParam('status', event.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(statusLabels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}
        </select>
        <div className="view-tabs">
          <button className={view === 'weak' ? 'active' : ''} aria-pressed={view === 'weak'}
            onClick={() => updateParam('view', view === 'weak' ? '' : 'weak')}>薄弱词</button>
          <button className={view === 'recent' ? 'active' : ''} aria-pressed={view === 'recent'}
            onClick={() => updateParam('view', view === 'recent' ? '' : 'recent')}>最近加入</button>
          <button className={view === 'stale' ? 'active' : ''} aria-pressed={view === 'stale'}
            onClick={() => updateParam('view', view === 'stale' ? '' : 'stale')}>长期未复习</button>
        </div>
      </div>
      {list.isLoading && <LoadingState />}
      {list.isError && <ErrorState error={list.error} action={
        <button className="button secondary" onClick={() => void list.refetch()}>重试</button>
      } />}
      {list.data && list.data.words.length === 0 && (
        <EmptyState title="没有找到单词"
          description={search || status || view ? '换一个搜索条件试试。' : '从单词书导入后，词条会出现在这里。'} />
      )}
      {list.data && list.data.words.length > 0 && (
        <div className="library-layout">
          <div className="word-list" ref={listRef}>
            <div className="word-list-head"><span>{list.data.total} 个词条</span><span>状态 / 复习</span></div>
            {list.data.words.map((word) => mobile ? (
              <Link className="word-row" key={word.word_state_id}
                data-word-state-id={word.word_state_id}
                to={`/library/${word.word_state_id}`}
                state={{ fromListKey: location.key, returnTo: location.pathname + location.search }}
                onClick={() => rememberPosition(word.word_state_id)}>
                <WordRowContent word={word} />
              </Link>
            ) : (
              <button className={selected === word.word_state_id ? 'selected' : ''}
                key={word.word_state_id} onClick={() => setSelected(word.word_state_id)}>
                <WordRowContent word={word} />
              </button>
            ))}
          </div>
          <aside className="word-detail">
            {!selected && <EmptyState title="选择一个单词" description="右侧会显示原书信息、学习数据和全部历史。" />}
            {detail.isLoading && <LoadingState />}
            {detail.isError && <ErrorState error={detail.error} />}
            {detail.data && <WordDetailContent word={detail.data} />}
          </aside>
        </div>
      )}
    </div>
  )
}
