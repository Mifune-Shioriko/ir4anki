// Wire shapes of the review-app FastAPI backend (app.py).

export interface SimilarCard {
  question: string
  answer?: string
  score: number
}

export interface Card {
  cardId: number
  noteId?: number
  question: string
  answer: string
  deckName: string
  modelName: string
  css: string
  isNew: boolean
  due: number
  similar?: SimilarCard[]
  /** AI-generated explanation from anki-explain (:8788); "" when missing */
  explanation?: string
}

export interface RoundInfo {
  state: 'active' | 'complete'
  done: number
  total: number
  due_remaining?: number
  new_per_round?: number
  new_total?: number
  /** pacing mode of the round (quick/focus) — 2026-09-14 */
  mode?: string
}

/** Pacing-mode table from the backend (user spec 2026-09-14, retuned
 * 2026-09-16): quick = 碎片时间 (5 preview + 5 new + 20 review),
 * focus = 整块时间 (10 preview + 10 new + 30 review).
 * `read` = 渐进制卡 reading chunks per round (user spec 2026-09-19;
 * absent on an old backend — treat as "no reading segment").
 * Sizes ALWAYS come from the wire — never hardcode them in UI copy. */
export interface StudyModeSizes {
  read?: number
  preview: number
  new: number
  review: number
}
export type StudyModes = Record<string, StudyModeSizes>

/** Preview-mode fields ride on every session/state response. When
 * preview_mode is false/absent the frontend renders the exact legacy UI. */
export interface PreviewRoundResume {
  cards: Card[]
  done: number
  total: number
  /** single-level undo slot survives page refreshes, like the review round */
  can_undo?: boolean
  /** approve/defer split — lets a resumed round report honest exit stats */
  approved?: number
  deferred?: number
  /** pacing mode of the resumed preview round (2026-09-14) */
  mode?: string
}

/** Exact reverse of a preview act: approve→card back in the pool suspended,
 * defer→today's deferred tag removed. 409 when there is nothing to undo or
 * the card moved on (e.g. already answered in a review round). */
export interface PreviewUndoResponse {
  restored: boolean
  card: Card
  index: number
  action?: 'approve' | 'defer'
}

export interface PreviewExtras {
  preview_mode?: boolean
  preview_pool?: number | null
  preview_available?: number
  preview_round?: PreviewRoundResume
  /** preview batch size of the ACTIVE preview round (mode-dependent since
   * 2026-09-14); null when no round — start screens use study_modes instead */
  preview_per_round?: number | null
  /** cards approved TODAY, still suspended — released into the study queue
   * tomorrow (next-day release, 2026-09-07) */
  pending_release?: number | null
  /** daily 放行 goal for the preview-start progress bar (2026-09-16) */
  release_daily_goal?: number | null
  /** remaining daily release slots (goal − approved today, 2026-09-16).
   * 0 = goal reached → no more preview rounds today; null = goal disabled */
  release_budget_left?: number | null
  /** pacing-mode table + default (2026-09-14) — the start screens render
   * the quick/focus choice from these, never hardcoded numbers */
  study_modes?: StudyModes
  default_mode?: string
}

export type SessionStateResponse = PreviewExtras & ReadingExtras & (
  | { state: 'none'; due_remaining: number | null; new_per_round?: number | null; new_total?: number | null; can_undo: boolean }
  | { state: 'complete'; done: number; total: number; new_in_batch?: number | null; due_remaining: number | null; new_per_round?: number | null; new_total?: number | null; can_undo: boolean; mode?: string }
  | { state: 'active'; cards: Card[]; done: number; total: number; new_in_batch?: number | null; due_remaining: number | null; new_per_round?: number | null; new_total?: number | null; can_undo: boolean; mode?: string }
)

export interface StartResponse {
  synced: boolean
  cards: Card[]
  due_remaining: number
  new_per_round: number
  new_total: number
  mode?: string
  study_modes?: StudyModes
}

export interface MoreResponse {
  cards: Card[]
  due_remaining: number
  new_per_round: number
  new_total: number
  mode?: string
  study_modes?: StudyModes
}

export interface PreviewStartResponse {
  cards: Card[]
  pool: number
  available: number
  mode?: string
  preview_per_round?: number
  /** true when the daily release budget is exhausted — no cards were dealt */
  goal_reached?: boolean
  /** capped pacing table (2026-09-16): preview sizes track remaining budget */
  study_modes?: StudyModes
  release_budget_left?: number | null
}

export interface PreviewActResponse {
  ok: boolean
  reason?: string
  round_complete?: boolean
  pool?: number
  available?: number
  /** refreshed on round_complete so the done screen's 再预览 N 张 is correct */
  study_modes?: StudyModes
  release_budget_left?: number | null
}

export interface AnswerResponse {
  answered: boolean
  round: RoundInfo | null
  /** present when answered=false: 'stale' = card no longer in this round */
  reason?: string
  /** double-Again auto-return (2026-09-16): a NEW card graded Again twice in
   * its first learning cycle was moved back to the preview pool */
  returned_to_preview?: { cardId: number; pool: number | null } | null
}

export interface UndoResponse {
  restored: boolean
  card: Card
  index: number
  /** set when the undone answer had auto-returned the card to the preview
   * pool (2026-09-16) — the card was moved back out, pool is the new size */
  returned_from_preview?: { cardId: number; pool: number | null } | null
}

export interface NoteResponse {
  cardId: number
  noteId: number
  modelName: string
  fields: Record<string, string>
  tags: string[]
}

/** One retrieved note section for the right-hand note panel (notes-rag
 * :8791 via /api/note/sections). line_start is the 1-based source line of
 * the section's heading — the panel scrolls+flashes [data-src-line] there. */
export interface NoteSection {
  file: string
  title: string
  heading_path: string[]
  line_start: number
  line_end: number
  year: number | null
  score: number
  snippet: string
}

export interface NoteSectionsResponse {
  sections: NoteSection[]
}

export interface NoteUpdateResponse {
  updated: boolean
  question: string
  answer: string
}

// ---- reading mode (渐进制卡, user spec 2026-09-19) ----

/** Chunk state machine (human-marked): todo → active(正在制卡) →
 * done(制卡完成), or skipped(无需制卡) from todo/active. */
export type ReadingChunkStatus = 'todo' | 'active' | 'done' | 'skipped'

/** One dealt reading chunk (wire shape of /api/reading/start chunks). */
export interface ReadingChunk {
  path: string
  chunk_key: string
  title: string
  heading_path: string[]
  line_start: number
  line_end: number
  text: string
  status: ReadingChunkStatus
  cards_created: number[]
  file_chunks: number
  file_done: number
  file_skipped: number
}

/** Per-file summary row of the 阅读清单 (/api/reading/status list). */
export interface ReadingFileSummary {
  path: string
  title: string
  missing?: boolean
  total_chunks: number
  todo: number
  active: number
  done: number
  skipped: number
  frontier: {
    chunk_key: string
    status: ReadingChunkStatus
    title: string
    line_start: number
  } | null
  orphans: number
  cards_created: number
}

export interface ReadingRoundStats {
  done?: number
  skipped?: number
  next?: number
}

/** Active round: chunks to render. Complete tombstone: stats only. */
export interface ReadingRound {
  status: 'active' | 'complete'
  chunks?: ReadingChunk[]
  done: number
  total: number
  stats?: ReadingRoundStats
  mode?: string
}

export interface ReadingStartResponse {
  chunks: ReadingChunk[]
  mode: string
  empty: boolean
  study_modes?: StudyModes
}

export interface ReadingActResponse {
  ok: boolean
  reason?: string
  status?: ReadingChunkStatus
  round_complete?: boolean
  stats?: ReadingRoundStats
  done?: number
  total?: number
}

export interface ReadingStatusResponse {
  reading_mode: boolean
  list: ReadingFileSummary[]
  round: ReadingRound | null
  available: number
  study_modes?: StudyModes
}

export interface ReadingStateResponse {
  reading_mode: boolean
  round: ReadingRound | null
  list: ReadingFileSummary[]
  available: number
}

export interface ReadingCorpusFile {
  path: string
  title: string
  in_list: boolean
}

/** Reading fields riding on /api/status + /api/session/state. */
export interface ReadingExtras {
  reading_mode?: boolean
  reading_list_size?: number
  reading_available?: number
  reading_active?: number
  /** active reading round only (resume after refresh) — the completed
   * tombstone is fetched via GET /api/reading/state */
  reading_round?: ReadingRound
}
