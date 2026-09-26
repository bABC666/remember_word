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
  // Every slot carries its own confirmer, and different slots may have been approved
  // by different people. Naming only the first one would attribute the rest to
  // someone who never saw them.
  const confirmers = [...new Set(meanings.map((meaning) => meaning.confirmed_by).filter(Boolean))]
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
            {/* A supplement has no source position by construction; keying on the
                flag as well as the field keeps the client honest on its own rather
                than only because the server cannot produce that shape. */}
            {!meaning.is_supplement && meaning.source_locator && (
              <small className="concise-locator">来源位置 {meaning.source_locator}</small>
            )}
          </li>
        ))}
      </ol>
      <small className="concise-confirmed-by">
        已由 {confirmers.join('、') || '管理员'} 人工确认
      </small>
    </section>
  )
}
