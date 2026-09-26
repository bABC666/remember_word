import type { ConciseMeaning } from '../types'

/**
 * The one to three short senses the study page prefers.
 *
 * Every value here has been confirmed by a person, but confirmed does **not** mean
 * "the source said this". `is_source_verbatim` is the only field that says so, and
 * anything else is labelled with where its wording came from: `据来源改写` for a
 * documented change to a source value, `自拟补充` for text no source contains. The
 * label is not decoration — the owner's rule is that a self-authored supplement must
 * never be presented as a source's own words, and a reader has to be able to tell
 * without opening anything else.
 */
export function ConciseMeaningList({
  values,
  label = '核心释义',
}: {
  values: ConciseMeaning[]
  label?: string
}) {
  if (!values.length) return null
  const meanings = [...values].sort((left, right) => left.display_order - right.display_order)
  return (
    <section className="concise-meanings" aria-label={label}>
      <span className="concise-label">{label}</span>
      <ol className="concise-meaning-list">
        {meanings.map((meaning) => (
          <li key={`${meaning.display_order}-${meaning.text}`}>
            <span className="concise-text">{meaning.text}</span>
            {!meaning.is_source_verbatim && (
              <span className={`concise-origin concise-origin-${meaning.provenance_kind}`}>
                {meaning.provenance_label}
              </span>
            )}
            {meaning.derivation_note && (
              <small className="concise-note">{meaning.derivation_note}</small>
            )}
            {meaning.source_locator && (
              <small className="concise-locator">来源位置 {meaning.source_locator}</small>
            )}
          </li>
        ))}
      </ol>
      <small className="concise-confirmed-by">
        已由 {meanings[0]?.confirmed_by || '管理员'} 人工确认
      </small>
    </section>
  )
}
