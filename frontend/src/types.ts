export type WordStatus = 'new' | 'familiar' | 'learning' | 'known' | 'weak' | 'mastered'
export type ReviewResult = 'know' | 'fuzzy' | 'fail'

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
  source_meanings: string[]
  source_raw: string
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
