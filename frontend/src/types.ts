export type WordStatus = 'new' | 'familiar' | 'learning' | 'known' | 'weak' | 'mastered'
export type ReviewResult = 'know' | 'fuzzy' | 'fail'

/**
 * One short, human-confirmed sense of a word, from
 * `entry_concise_meaning`. Separate from `source_meanings` on purpose: the source
 * default is what the source said, this is what the study page shows, and a
 * supplement nobody else wrote must be able to say so without pretending otherwise.
 */
export interface ConciseMeaning {
  text: string
  display_order: number
  /** Where the *wording* came from: the source itself, a change to it, or neither. */
  provenance_kind: 'source' | 'derived' | 'ai_supplement'
  /** The human-readable label for `provenance_kind`, chosen by the server. */
  provenance_label: string
  /** True only when the text is the source's own value; never inferred on the client. */
  is_source_verbatim: boolean
  is_supplement: boolean
  /** `primary:12`-style position in the source, empty for a supplement. */
  source_locator: string
  /**
   * The evidence row this wording was proposed against, or null when it has none: a
   * supplement is structurally forbidden from carrying one, and a value proposed
   * before an import recorded evidence has only `source_locator`.
   *
   * Declared but deliberately not consumed by the page yet. A derived or quoted value
   * may cite a row the import recorded and did **not** adopt, so linking a displayed
   * value to a source from this id alone would be the very mistake the display design
   * warns about -- an unadopted candidate standing in as the origin of what is shown.
   * Reading it correctly means checking the row against `sources.fields`.
   */
  source_evidence_id: number | null
  /** What was changed, or why a supplement was added. */
  derivation_note: string
  confirmed_by: string
  confirmed_at: string | null
}

export interface Word {
  /**
   * The V1.1 `word.id`, or null for a word added in V1.2 (one added from an
   * article has no `word` row). It is not interchangeable with `word_state_id`:
   * the legacy routes take only this, and they never fall back to the state id.
   */
  id: number | null
  /** This user's own `user_word_state.id`. Always present for a word the user has. */
  word_state_id: number
  legacy_word_id: number | null
  lexicon_entry_id: number
  lexicon_id: number
  word: string
  phonetic: string
  part_of_speech: string
  /** The source's adjudicated default. Never overwritten by a short display value. */
  source_meanings: string[]
  /** The primary source's own line, verbatim. Still the thing to check a value against. */
  source_raw: string
  /**
   * The short, confirmed display values, in order. An empty array means nothing has
   * been confirmed for this word: fall back to `source_meanings`/`source_raw`, and
   * never fill the gap with an unreviewed candidate.
   */
  concise_meanings: ConciseMeaning[]
  anchor: string
  semantic_note: string
  status: WordStatus
  first_seen: string
  last_review: string | null
  next_review_at: string | null
  recall_success: number
  recall_fail: number
  context_exposure: number
  possible_issue: boolean
  notes: string
}

export interface Candidate {
  id: number
  word: string
  phonetic: string
  part_of_speech: string
  source_meanings: string[]
  source_raw: string
  anchor: string
  semantic_note: string
  possible_issue: boolean
  issue_note: string
  selected: boolean
  confirmed: boolean
}

export interface ImportBatch {
  id: number
  status: string
  stage: string
  created_at: string
  updated_at: string
  raw_ocr_text: string
  error_stage: string
  error_message: string
  images: Array<{ id: number; original_name: string; width: number; height: number; ocr_text: string; error_message: string; is_deleted: boolean }>
  candidates: Candidate[]
}

export interface Article {
  id: number
  title: string
  content: string
  created_at: string
  target_words: string[]
  actual_used_words?: string[]
  completed: boolean
  translation?: string
  translated_at?: string | null
  lookup_history?: ArticleWordLookup[]
  quiz_words?: Array<Word & { context: string }>
}

export interface ArticleWordLookup {
  id: number
  article_id: number
  surface: string
  normalized_word: string
  phonetic: string
  part_of_speech: string
  meaning: string
  explanation: string
  context: string
  source: 'wordbook' | 'ai'
  added_word_id: number | null
  created_at: string
}

/** One source value the import recorded for one field, and what it decided about it. */
export interface SourceEvidence {
  source_evidence_id: number
  /** `word` | `meaning` | `phonetic` | `part_of_speech`, as the import recorded it. */
  field_kind: string
  /** Physical line in the source file the value was read from. */
  row_locator: number
  sense_key: string
  /** The source's own text for this field, verbatim. */
  raw_text: string
  /** `selected` | `not_selected`, mirroring `selected_for_default`. */
  decision: string
  /** Whether the written content came from this row. The field the split is made on. */
  selected_for_default: boolean
  selection_order: number | null
  /** The pinned revision of the source page, or empty when none was recorded. */
  source_revision: string
  /**
   * The server's back-check link for that exact revision, or empty when it could not
   * build one honestly: no revision was recorded, or the frozen mapping declares no
   * usable template. Never a partial URL, and never something a client should assemble
   * -- an empty string means "show the state, offer no link".
   */
  source_revision_url: string
  /** What the import recorded about the artifact this row belongs to. */
  source: {
    source_artifact_id: number
    role: string
    name: string
    publisher: string
    version: string
    license_id: string
  }
}

/**
 * One field's evidence, with the rows a human adopted kept apart from the rest.
 *
 * The import records a row for every value it considered, so `candidates` is not
 * noise: it is what a reader may consult. It is a separate list because a row nobody
 * adopted must never stand beside the value that is actually displayed.
 */
export interface SourceFieldGroup {
  field_kind: string
  selected: SourceEvidence[]
  candidates: SourceEvidence[]
}

/**
 * Which item of the source record is missing, in the server's own words.
 *
 * `status` is `complete` only when every adopted row has a revision and a link, so an
 * empty `fields` list cannot be mistaken for "nothing to say".
 */
export interface SourceCompleteness {
  status: 'complete' | 'incomplete'
  missing: Array<{ code: string; field_kind: string; message: string }>
  message: string
}

export interface EntrySources {
  fields: SourceFieldGroup[]
  completeness: SourceCompleteness
}
