import { useDeferredValue, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { WordDetailContent } from './WordDetailPage'
import { statusLabels, type LibraryPosition, type LibraryWord, type WordDetail } from './wordDetailModel'

const MOBILE_QUERY = '(max-width: 900px)'
const views = ['weak', 'recent', 'stale'] as const
const PAGE_SIZE = 50

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

function WordRowContent({ word }: { word: LibraryWord }) {
  return (
    <>
      <div>
        <strong>{word.word}</strong>
        <small className="phonetic">{word.phonetic} · {word.part_of_speech}</small>
      </div>
      <div>
        <span className={`status status-${word.status}`}>{word.status === 'new' ? '未学习' : statusLabels[word.status]}</span>
        <small>{word.last_review ? new Date(word.last_review).toLocaleDateString('zh-CN') : '尚未复习'}</small>
      </div>
    </>
  )
}

export function LibraryPage({ positions }: { positions: Map<string, LibraryPosition> }) {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const mobile = useMobileLayout()
  const [selected, setSelected] = useState<LibraryWord | null>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const search = params.get('search') ?? ''
  const rawStatus = params.get('status') ?? ''
  const status = rawStatus === 'unstudied' || Object.hasOwn(statusLabels, rawStatus) ? rawStatus : ''
  const rawView = params.get('view') ?? ''
  const view = views.some((item) => item === rawView) ? rawView : ''
  const deferredSearch = useDeferredValue(search)
  const rawPage = Number(params.get('page') ?? 1)
  const page = Number.isSafeInteger(rawPage) && rawPage >= 1 ? rawPage : 1
  const list = useQuery({
    queryKey: ['words', deferredSearch, status, view, page],
    queryFn: () => api<{ total: number; words: LibraryWord[]; lexicon?: { id: number; name: string } | null }>(
      `/api/words?search=${encodeURIComponent(deferredSearch)}&status=${status}&view=${view}&catalog=true&limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}`
    ),
  })
  const selectedWord = selected && (!list.data?.lexicon || list.data.lexicon.id === selected.lexicon_id) ? selected : null
  const entryDetail = selectedWord?.word_state_id === null
  const selectedId = entryDetail ? selectedWord?.lexicon_entry_id : selectedWord?.word_state_id
  const detail = useQuery({
    queryKey: [entryDetail ? 'entry-word' : 'word', selectedId],
    queryFn: () => api<WordDetail>(`/api/words/${entryDetail ? 'entry' : 'state'}/${selectedId}`),
    enabled: !mobile && selectedWord !== null,
  })

  const updateParam = (name: 'search' | 'status' | 'view' | 'page', value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(name, value)
    else next.delete(name)
    if (name !== 'page') next.delete('page')
    setSelected(null)
    setParams(next, { replace: true, state: location.state })
  }

  const rememberPosition = (word: LibraryWord) => {
    positions.set(location.key, {
      listTop: listRef.current?.scrollTop ?? 0,
      pageY: window.scrollY,
      focusedId: word.word_state_id,
      focusedEntryId: word.lexicon_entry_id,
    })
  }

  useLayoutEffect(() => {
    const restoreFromKey = (location.state as { restoreFromKey?: string } | null)?.restoreFromKey
    const positionKey = restoreFromKey && positions.has(restoreFromKey) ? restoreFromKey : location.key
    const previous = positions.get(positionKey)
    if (!previous || !list.data || !listRef.current) return
    listRef.current.scrollTop = previous.listTop
    window.scrollTo(0, previous.pageY)
    const row = [...listRef.current.querySelectorAll<HTMLAnchorElement>('a[data-entry-id]')]
      .find((item) => previous.focusedId !== null
        ? item.dataset.wordStateId === String(previous.focusedId)
        : item.dataset.entryId === String(previous.focusedEntryId))
    if (row) row.focus({ preventScroll: true })
    else titleRef.current?.focus({ preventScroll: true })
    // Keep the source entry while its detail route remains in browser history.
  }, [list.data, location.key, location.state, positions])

  return (
    <div className="page library-page">
      <header className="page-head">
        <h1 ref={titleRef} tabIndex={-1}>我的词库</h1>
        <p>查看每个词的原始资料、学习状态与完整历史。</p>
        {list.data?.lexicon && <p>当前词库：{list.data.lexicon.name} · 包含全部词条</p>}
        <Link to="/lexicons">选择或导入自定义词库</Link>
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
          <option value="unstudied">未学习</option>
          {rawStatus === 'new' && <option value="new">新词</option>}
          {Object.entries(statusLabels).filter(([key]) => key !== 'new').map(([key, label]) => <option value={key} key={key}>{label}</option>)}
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
      {list.data && list.data.total > PAGE_SIZE && <nav className="library-pagination" aria-label="词库分页">
        <button className="button secondary" disabled={page <= 1 || list.isFetching} onClick={() => updateParam('page', page === 2 ? '' : String(page - 1))}>上一页</button>
        <span>第 {page} / {Math.ceil(list.data.total / PAGE_SIZE)} 页 · 每页 {PAGE_SIZE} 词</span>
        <button className="button secondary" disabled={page * PAGE_SIZE >= list.data.total || list.isFetching} onClick={() => updateParam('page', String(page + 1))}>下一页</button>
      </nav>}
      {list.isLoading && <LoadingState />}
      {list.isError && <ErrorState error={list.error} action={
        <button className="button secondary" onClick={() => void list.refetch()}>重试</button>
      } />}
      {list.data && list.data.words.length === 0 && (
        <EmptyState title="没有找到单词"
          description={search || status || view ? '换一个搜索条件试试。' : '请先选择词库，或导入自己的词库。'} />
      )}
      {list.data && list.data.words.length > 0 && (
        <div className="library-layout">
          <div className="word-list" ref={listRef}>
            <div className="word-list-head"><span>{list.data.total} 个词条</span><span>状态 / 复习</span></div>
            {list.data.words.map((word) => mobile ? (
              <Link className="word-row" key={word.lexicon_entry_id}
                data-word-state-id={word.word_state_id}
                data-entry-id={word.lexicon_entry_id}
                to={word.word_state_id === null ? `/library/entry/${word.lexicon_entry_id}` : `/library/${word.word_state_id}`}
                state={{ fromListKey: location.key, returnTo: location.pathname + location.search }}
                onClick={() => rememberPosition(word)}>
                <WordRowContent word={word} />
              </Link>
            ) : (
              <button className={selectedWord?.lexicon_entry_id === word.lexicon_entry_id ? 'selected' : ''}
                key={word.lexicon_entry_id} onClick={() => setSelected(word)}>
                <WordRowContent word={word} />
              </button>
            ))}
          </div>
          <aside className="word-detail">
            {!selectedWord && <EmptyState title="选择一个单词" description="右侧会显示原书信息、学习数据和全部历史。" />}
            {detail.isLoading && <LoadingState />}
            {detail.isError && <ErrorState error={detail.error} />}
            {detail.data && <WordDetailContent word={detail.data} />}
          </aside>
        </div>
      )}
    </div>
  )
}
