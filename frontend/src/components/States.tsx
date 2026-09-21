import type { ReactNode } from 'react'
import { AlertCircle, Inbox, LoaderCircle } from 'lucide-react'

export function LoadingState({ label = '正在加载…' }: { label?: string }) {
  return <div className="state-block"><LoaderCircle className="spin" size={22} /><span>{label}</span></div>
}

export function ErrorState({ error, action }: { error: unknown; action?: ReactNode }) {
  const message = error instanceof Error ? error.message : '发生了未知错误'
  return <div className="state-block state-error"><AlertCircle size={22} /><div><strong>暂时无法完成</strong><p>{message}</p>{action}</div></div>
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><Inbox size={30} /><h2>{title}</h2><p>{description}</p>{action}</div>
}
