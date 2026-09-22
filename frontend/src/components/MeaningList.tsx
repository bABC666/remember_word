import { splitMeanings } from '../meaningText'

export function MeaningList({ values, fallback = '' }: { values: string[]; fallback?: string }) {
  const meanings = splitMeanings(values)
  if (!meanings.length && !fallback) return <p className="muted">暂无释义</p>
  return <ol className="meaning-list">{(meanings.length ? meanings : [fallback]).map((meaning, index) => <li key={`${meaning}-${index}`}>{meaning}</li>)}</ol>
}
