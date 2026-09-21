import type { ReactNode } from 'react'
import { BookOpen, Home, Import, LibraryBig, Settings, Sparkles } from 'lucide-react'
import { NavLink } from 'react-router-dom'

const items = [
  { to: '/', label: '今日概览', icon: Home },
  { to: '/import', label: '单词导入', icon: Import },
  { to: '/study', label: '今日学习', icon: BookOpen },
  { to: '/reading', label: '阅读练习', icon: Sparkles },
  { to: '/library', label: '我的词库', icon: LibraryBig },
  { to: '/settings', label: '设置', icon: Settings },
]

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">拾</span><div><strong>拾词</strong><small>在阅读中，看见更大的世界。</small></div></div>
        <nav aria-label="主导航">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}>
              <Icon size={20} strokeWidth={1.8} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot"><span className="status-dot" />数据已本地保存<small>专注积累，静待改变。</small></div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  )
}
