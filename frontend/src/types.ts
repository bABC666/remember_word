export type WordStatus = 'new' | 'familiar' | 'learning' | 'known' | 'weak' | 'mastered'
export type ReviewResult = 'know' | 'fuzzy' | 'fail'

export interface Word {
  id: number
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
  images: Array<{ id: number; original_name: string; width: number; height: number; ocr_text: string; error_message: string }>
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
  quiz_words?: Array<Word & { context: string }>
}
