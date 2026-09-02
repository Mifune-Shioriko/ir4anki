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
  new_quota_left?: number
  new_quota_total?: number
  new_total?: number
}

export type SessionStateResponse =
  | { state: 'none'; due_remaining: number | null; new_quota_left: number | null; new_quota_total?: number | null; new_total?: number | null; can_undo: boolean }
  | { state: 'complete'; done: number; total: number; new_in_batch?: number | null; due_remaining: number | null; new_quota_left?: number | null; new_quota_total?: number | null; new_total?: number | null; can_undo: boolean }
  | { state: 'active'; cards: Card[]; done: number; total: number; new_in_batch?: number | null; due_remaining: number | null; new_quota_left?: number | null; new_quota_total?: number | null; new_total?: number | null; can_undo: boolean }

export interface StartResponse {
  synced: boolean
  cards: Card[]
  due_remaining: number
  new_quota_left: number
  new_quota_total: number
  new_total: number
}

export interface MoreResponse {
  cards: Card[]
  due_remaining: number
  new_quota_left: number
  new_quota_total: number
  new_total: number
}

export interface AnswerResponse {
  answered: boolean
  round: RoundInfo | null
  /** present when answered=false: 'stale' = card no longer in this round */
  reason?: string
}

export interface UndoResponse {
  restored: boolean
  card: Card
  index: number
}

export interface NoteResponse {
  cardId: number
  noteId: number
  modelName: string
  fields: Record<string, string>
  tags: string[]
}

export interface NoteUpdateResponse {
  updated: boolean
  question: string
  answer: string
}
