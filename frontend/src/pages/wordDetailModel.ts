import type { Word } from '../types'

export type WordDetail = Word & {
  review_history: Array<{
    id: number
    timestamp: string
    result: string
    status_before: string
    status_after: string
    source: string
  }>
  article_exposures: Array<{
    article_id: number
    context: string
    exposure_count?: number
    first_exposed_at?: string
    last_exposed_at?: string
  }>
}

export type LibraryPosition = {
  listTop: number
  pageY: number
  focusedId: number
}

export const statusLabels: Record<string, string> = {
  new: '新词', familiar: '眼熟', learning: '学习中',
  known: '已知', weak: '薄弱', mastered: '已掌握',
}
