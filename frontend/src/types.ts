export type WordStatus = 'new' | 'familiar' | 'learning' | 'known' | 'weak' | 'mastered'
export type ReviewResult = 'know' | 'fuzzy' | 'fail'

export interface PublicAttribution {
  creators: string
  modifications: string
  license_url: string
  source_url?: string
  copyright_notice?: string
  disclaimer?: string
  snapshot_label?: string
  snapshot_url?: string
  snapshot_sha256?: string
  links?: Array<{ label: string; url: string }>
}

/**
 * One additional source position a displayed value rests on.
 *
 * A value can rest on more than one position and on more than one source: the trial
 * record's `decrease` merges a zh.wiktionary line with a WikDict value. The primary
 * citation is the meaning's own `source_locator` / `source_evidence_id`; these are the
 * **additional** ones, in `citation_order`.
 */
export interface ConciseMeaningCitation {
  /** 1-based position in this value's citation list. */
  citation_order: number
  /** `zhwiktionary:7993707:15`-style position in the source. */
  citation_locator: string
  /** The evidence row the position resolved to at import time, or null. */
  source_evidence_id: number | null
}

/**
 * One short, evidence-confirmed sense of a word, from
 * `entry_concise_meaning`. Separate from `source_meanings` on purpose: the source
 * default is what the source said, this is what the study page shows, and a
 * supplement nobody else wrote must be able to say so without pretending otherwise.
 */
export interface ConciseMeaning {
  text: string
  /** Position **within its part-of-speech group**, 1–3. */
  display_order: number
  /** Where the *wording* came from: the source itself, a change to it, or neither. */
  provenance_kind: 'source' | 'derived' | 'ai_supplement'
  /** The human-readable label for `provenance_kind`, chosen by the server. */
  provenance_label: string
  /** True only when the text is the source's own value; never inferred on the client. */
  is_source_verbatim: boolean
  is_supplement: boolean
  /** The **primary** `primary:12`-style position in the source, empty for a supplement. */
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
  /** Every **additional** source position, in `citation_order`. Empty for a supplement. */
  citations: ConciseMeaningCitation[]
  /** What was changed, or why a supplement was added. */
  derivation_note: string
  confirmed_by: string
  confirmed_at: string | null
}

/**
 * One part-of-speech group of short display values.
 *
 * The server groups by `pos_key`, sorts groups by `pos_order` and returns each group's
 * values in `display_order`, so a client renders the list as received rather than
 * re-deriving an order from two numbers. `pos_order` is repeated on the group so the
 * order survives any client-side re-sorting.
 *
 * `pos_source` says how the part of speech was established — `pos_section` from a
 * heading in the pinned revision, `reviewer` from a person's judgement — and is
 * therefore also a statement about how much the label is worth. A group whose part of
 * speech a person judged is marked as such rather than shown like a sourced one.
 */
export interface ConciseMeaningGroup {
  pos_key: string
  /** Display label, e.g. `动词`. Falls back to `pos_key` server-side when unset. */
  pos_label: string
  pos_source: 'none' | 'pos_section' | 'reviewer'
  /** The server's wording for `pos_source`, so the two cannot drift. */
  pos_source_label: string
  /** Which group comes first; the list arrives in this order already. */
  pos_order: number
  /** The group's 1–3 values, in `display_order`. */
  meanings: ConciseMeaning[]
}

export interface DictionaryExtractionValue {
  text: string
  pos_key?: string
  pos_label?: string
  language: string
  locator: string
  status: string
  raw_text?: string
  reason?: string
  semantic_scope?: 'sense' | 'word_translation'
  meaning_kind?: 'source' | 'derived'
  /** Usage restrictions read from the cited dictionary definition. */
  display_usage_labels?: string[]
  accent_status?: string
  quality_review?: { note: string; kind?: string; core_confirmation?: boolean; alignment_note?: string }
  pos_raw?: string
  pos_locator?: string
  source: {
    publisher: string
    license_id?: string
    version?: string
    original_file_sha256?: string
    body_sha256?: string
    revision?: string
    mapping_json?: string
    attribution?: PublicAttribution
    extraction_modifications?: string
  }
}

export interface DictionaryExtraction {
  entry_id?: number
  audit_status?: 'automated_source_verified'
  excluded_count?: number
  parser_version: string
  status: 'unconfirmed_source_extraction'
  senses: DictionaryExtractionValue[]
  pronunciations: DictionaryExtractionValue[]
  pending_count: number
  pending_reasons: string[]
  pending_values?: DictionaryExtractionValue[]
  originals?: Array<{ raw_text: string; source: DictionaryExtractionValue['source'] }>
}

export interface Word {
  dictionary_extraction?: DictionaryExtraction | null
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
  meaning_origin?: 'user_provided' | 'platform'
  /** Adopted dictionary evidence for study display; absent for private uploads. */
  source_meaning_sources?: Array<{
    name?: string
    source_artifact_id?: number
    file_sha256?: string
    publisher: string
    version: string
    source_position: string
    import_csv_line: string
    source_revision: string
    source_revision_url: string
    license_id?: string
    attribution?: PublicAttribution
    source_history_url?: string
  }>
  /**
   * The short, confirmed display values, grouped by part of speech and ordered by
   * `pos_order`, with each group's values in `display_order`. An empty array means
   * nothing is displayable for this word -- no confirmed value, or none that passed the
   * server's display gate. Source text remains separate and must not become a core
   * meaning or an unmarked answer when this array is empty.
   */
  concise_meanings: ConciseMeaningGroup[]
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
  source_history_url?: string
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
    attribution?: PublicAttribution
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
