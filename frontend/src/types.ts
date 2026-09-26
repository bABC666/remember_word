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
