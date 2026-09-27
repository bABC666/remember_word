import type { EntrySources, Word } from '../types'

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
    first_exposed_at?: string | null
    last_exposed_at?: string | null
  }>
  /**
   * The entry's per-field source record. `GET /api/words/state/{id}` always carries
   * it; the legacy `GET /api/words/{id}` route does not, and no component here renders
   * that route's payload.
   */
  sources: EntrySources
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
