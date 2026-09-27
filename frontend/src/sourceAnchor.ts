/**
 * The one place that decides what a source card's fragment is called.
 *
 * A source artifact is a row about a file read through a mapping, and it is **not**
 * owned by a lexicon: importing the same file, mapping and role into a second lexicon
 * reuses the same artifact row. So `source-<artifact>` is not unique on a page that
 * lists several lexicons -- and a duplicate id makes the second card unreachable as a
 * fragment target, which is how the word detail's internal link used to land in the
 * wrong lexicon's section.
 *
 * The anchor therefore carries both ids, and both sides build it here: the card on
 * `/sources` and the link from the word detail. Two copies of this format is how the
 * two would drift apart again.
 */
export function sourceCardAnchor(lexiconId: number, sourceArtifactId: number): string {
  return `source-${lexiconId}-${sourceArtifactId}`
}

/** Where a reader is sent to read one source card: the page, plus its fragment. */
export function sourceCardHref(lexiconId: number, sourceArtifactId: number): string {
  return `/sources#${sourceCardAnchor(lexiconId, sourceArtifactId)}`
}
