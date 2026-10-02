import type { ConciseMeaningGroup } from '../types'

/**
 * The short senses the study page prefers, grouped by part of speech.
 *
 * The server sends the groups already ordered by `pos_order` and each group's values
 * already ordered by `display_order`; the sorts here are a belt-and-braces repeat of
 * that contract rather than the source of the order, so a group is never rendered in
 * whatever order the JSON happened to arrive in.
 *
 * Every value here has been confirmed in the review workflow, but confirmed does **not** mean
 * "the source said this". `is_source_verbatim` is the only field that says so, and
 * anything else is labelled with where its wording came from: `据来源改写` for a
 * documented change to a source value, `自拟补充` for text no source contains. The
 * label is not decoration — the owner's rule is that a self-authored supplement must
 * never be presented as a source's own words, and a reader has to be able to tell
 * without opening anything else. A supplement therefore shows **no** source position at
 * all, primary or additional, even if one is somehow present in the payload: the
 * server cannot produce that shape, and the component must not depend on that to stay
 * honest.
 */

/**
 * Shown beside a group whose part of speech a person judged rather than a source
 * heading. `pos_source === 'reviewer'` is the client's own check, because that is the
 * fact being marked; the server's own wording for it (`pos_source_label`) goes in the
 * element's tooltip, so the two are available without printing two differently-worded
 * badges side by side.
 */
const REVIEWER_BASIS_NOTE = '词性试判'

export function ConciseMeaningList({
  groups,
  label = '核心释义',
}: {
  groups: ConciseMeaningGroup[]
  label?: string
}) {
  if (!groups.length) return null
  // The server's order, restated: a client that rendered the array as received would
  // be depending on JSON member order for something the contract actually promises.
  const ordered = [...groups].sort((left, right) => left.pos_order - right.pos_order)
  // Every value carries its own confirmer, and different values may have been approved
  // by different actors across groups. Naming only the first would misattribute the rest.
  const confirmers = [
    ...new Set(
      ordered
        .flatMap((group) => group.meanings)
        .map((meaning) => meaning.confirmed_by)
        .filter(Boolean),
    ),
  ]
  return (
    <section className="concise-meanings" aria-label={label}>
      <span className="concise-label">{label}</span>
      {ordered.map((group) => (
        <div className="concise-group" key={group.pos_key}>
          <span className="concise-pos-head">
            <span className="concise-pos">{group.pos_label}</span>
            {group.pos_source === 'reviewer' && (
              <span className="concise-pos-basis" title={group.pos_source_label}>
                {REVIEWER_BASIS_NOTE}
              </span>
            )}
          </span>
          <ol className="concise-meaning-list">
            {[...group.meanings]
              .sort((left, right) => left.display_order - right.display_order)
              .map((meaning) => (
                <li key={`${group.pos_key}-${meaning.display_order}-${meaning.text}`}>
                  <span className="concise-text">{meaning.text}</span>
                  {!meaning.is_source_verbatim && (
                    <span
                      className={`concise-origin concise-origin-${meaning.provenance_kind}`}
                    >
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
                    <small className="concise-locator">
                      来源位置 {meaning.source_locator}
                    </small>
                  )}
                  {!meaning.is_supplement && meaning.citations.length > 0 && (
                    <small className="concise-citations">
                      附加引用
                      {[...meaning.citations]
                        .sort((left, right) => left.citation_order - right.citation_order)
                        .map((citation) => (
                          <span
                            className="concise-citation"
                            key={`${citation.citation_order}-${citation.citation_locator}`}
                          >
                            {citation.citation_locator}
                          </span>
                        ))}
                    </small>
                  )}
                </li>
              ))}
          </ol>
        </div>
      ))}
      <small className="concise-confirmed-by">
        释义裁定：{confirmers.join('、') || '管理员'}
      </small>
    </section>
  )
}
