import type {
  AnswerResponse,
  MoreResponse,
  NoteResponse,
  NoteUpdateResponse,
  SessionStateResponse,
  StartResponse,
  UndoResponse,
} from './types'

async function parse<T>(r: Response): Promise<T> {
  const data = (await r.json().catch(() => null)) as (T & { detail?: string }) | null
  if (!r.ok) throw new Error(data?.detail || `HTTP ${r.status}`)
  return data as T
}

function get<T>(url: string): Promise<T> {
  return fetch(url).then(r => parse<T>(r))
}

function post<T>(url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method: 'POST' }
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }
  return fetch(url, init).then(r => parse<T>(r))
}

export const api = {
  sessionState: () => get<SessionStateResponse>('/api/session/state'),
  start: () => post<StartResponse>('/api/session/start'),
  more: () => post<MoreResponse>('/api/session/more'),
  finish: () => post<{ synced: boolean }>('/api/session/finish'),
  answer: (cardId: number, ease: number) =>
    post<AnswerResponse>(`/api/answer?card_id=${cardId}&ease=${ease}`),
  undo: () => post<UndoResponse>('/api/undo'),
  note: (cardId: number) => get<NoteResponse>(`/api/note?card_id=${cardId}`),
  tags: () => get<{ tags: string[] }>('/api/tags'),
  updateNote: (cardId: number, fields: Record<string, string>, tags: string[]) =>
    post<NoteUpdateResponse>('/api/note/update', { card_id: cardId, fields, tags }),
  upload: (file: File): Promise<{ filename: string }> => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch('/api/media/upload', { method: 'POST', body: fd }).then(r =>
      parse<{ filename: string }>(r),
    )
  },
}
