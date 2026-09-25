import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  BookOpen, CircleHelp, Home, Import, LibraryBig, LogOut, Menu, Settings, Sparkles, X,
} from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../useAuth'
import { HelpCenter } from './HelpCenter'

const items = [
  { to: '/', label: '今日概览', icon: Home },
  { to: '/import', label: '单词导入', icon: Import },
  { to: '/study', label: '今日学习', icon: BookOpen },
  { to: '/reading', label: '阅读练习', icon: Sparkles },
  { to: '/library', label: '我的词库', icon: LibraryBig },
  { to: '/settings', label: '设置', icon: Settings },
]

/**
 * F-6: the phone bottom bar keeps four destinations plus a "更多" sheet.
 *
 * Six entries at 390px measured about 65px each and, worse, pushed the account
 * block -- and with it the only sign-out control -- below the viewport, so a
 * phone could not sign out at all. Import, settings, help and sign-out move into
 * the sheet instead of competing for bar width; the four destinations that stay
 * keep their routes and their labels.
 */
const BAR_PATHS = ['/', '/study', '/reading', '/library']
/** Destinations the sheet owns; being on one of them marks "更多" as current. */
const SHEET_PATHS = ['/import', '/settings']
const COMPACT_QUERY = '(max-width: 620px)'

/** True only where the sidebar becomes the fixed bottom bar. */
function useCompactBar() {
  const [compact, setCompact] = useState(() => window.matchMedia?.(COMPACT_QUERY).matches ?? false)
  useEffect(() => {
    const query = window.matchMedia?.(COMPACT_QUERY)
    if (!query) return
    const onChange = (event: MediaQueryListEvent) => setCompact(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return compact
}

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const location = useLocation()
  const compact = useCompactBar()
  const [moreOpen, setMoreOpen] = useState(false)
  const [helpRequest, setHelpRequest] = useState(0)
  const moreButton = useRef<HTMLButtonElement>(null)
  const sheet = useRef<HTMLElement>(null)

  const closeMore = useCallback(() => {
    setMoreOpen(false)
    moreButton.current?.focus()
  }, [])

  // A wide viewport has no sheet, and a navigation already decided what the user
  // wants to look at: neither may leave the sheet hanging over the page.
  useEffect(() => {
    if (!compact) setMoreOpen(false)
  }, [compact])
  useEffect(() => {
    setMoreOpen(false)
  }, [location.pathname, location.search])

  useEffect(() => {
    if (!moreOpen) return
    sheet.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeMore()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [moreOpen, closeMore])

  const openHelp = () => {
    setMoreOpen(false)
    setHelpRequest((value) => value + 1)
  }
  const barItems = compact ? items.filter((item) => BAR_PATHS.includes(item.to)) : items
  const inSheet = SHEET_PATHS.some((path) => location.pathname === path)

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">拾</span><div><strong>拾词</strong><small>在阅读中，看见更大的世界。</small></div></div>
        <nav aria-label="主导航">
          {barItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              // Below 900px the label is `display: none`, which would leave an
              // icon-only entry with no accessible name at all.
              aria-label={label}
              className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}
            >
              <Icon size={20} strokeWidth={1.8} /><span>{label}</span>
            </NavLink>
          ))}
          {compact && (
            <button
              ref={moreButton}
              type="button"
              className={moreOpen || inSheet ? 'nav-link nav-more active' : 'nav-link nav-more'}
              aria-label="更多"
              aria-expanded={moreOpen}
              aria-haspopup="dialog"
              aria-controls="mobile-more"
              onClick={() => setMoreOpen(true)}
            >
              <Menu size={20} strokeWidth={1.8} /><span>更多</span>
            </button>
          )}
        </nav>
        <HelpCenter openRequest={helpRequest} />
        <div className="sidebar-account">
          <div className="sidebar-account-name">
            <strong>{user?.display_name || user?.username || ''}</strong>
            {user?.is_admin && <small>管理员</small>}
          </div>
          <button className="account-logout" type="button" onClick={() => void logout()} aria-label="退出登录">
            <LogOut size={16} strokeWidth={1.8} />
            <span>退出</span>
          </button>
        </div>
        <div className="sidebar-foot"><span className="status-dot" />数据已本地保存<small>专注积累，静待改变。</small></div>
      </aside>
      {compact && moreOpen && (
        <div className="more-backdrop" role="presentation" onClick={closeMore}>
          <section
            id="mobile-more"
            ref={sheet}
            className="more-sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mobile-more-title"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="more-sheet-head">
              <h2 id="mobile-more-title">更多</h2>
              <button className="more-close" type="button" aria-label="关闭更多" onClick={closeMore}><X size={18} /></button>
            </div>
            <p className="more-account">
              {user?.display_name || user?.username || ''}{user?.is_admin ? ' · 管理员' : ''}
            </p>
            <nav className="more-links" aria-label="更多入口">
              <NavLink to="/import" className="more-link" onClick={() => setMoreOpen(false)}>
                <Import size={18} strokeWidth={1.8} /><span>单词导入</span>
              </NavLink>
              <NavLink to="/settings" className="more-link" onClick={() => setMoreOpen(false)}>
                <Settings size={18} strokeWidth={1.8} /><span>设置</span>
              </NavLink>
              <button className="more-link" type="button" onClick={openHelp}>
                <CircleHelp size={18} strokeWidth={1.8} /><span>使用说明</span>
              </button>
            </nav>
            <button
              className="more-link danger"
              type="button"
              onClick={() => { setMoreOpen(false); void logout() }}
            >
              <LogOut size={18} strokeWidth={1.8} /><span>退出登录</span>
            </button>
          </section>
        </div>
      )}
      <main className="main-content">{children}</main>
    </div>
  )
}
