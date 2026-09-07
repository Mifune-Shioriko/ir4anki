import type {
  AnswerResponse,
  MoreResponse,
  NoteResponse,
  NoteUpdateResponse,
  PreviewActResponse,
  PreviewStartResponse,
  PreviewUndoResponse,
  SessionStateResponse,
  StartResponse,
  UndoResponse,
} from './types'

async function parse<T>(r: Response): Promise<T> {
  // Read as text first: unknown API paths against an OLD backend process
  // fall through the SPA catch-all and return index.html with HTTP 200 —
  // silently accepting that would let callers treat a failed call as
  // success (e.g. a delete that never happened).
  const text = await r.text()
  let data: (T & { detail?: string }) | null = null
  try {
    data = JSON.parse(text)
  } catch {
    /* non-JSON body (SPA fallback HTML, gateway error page…) */
  }
  if (!r.ok) throw new Error(data?.detail || `HTTP ${r.status}`)
  if (data === null) throw new Error('后端响应异常（页面刷新或重启服务后再试）')
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
  previewStart: () => post<PreviewStartResponse>('/api/preview/start'),
  previewAct: (cardId: number, action: 'approve' | 'defer') =>
    post<PreviewActResponse>(`/api/preview/act?card_id=${cardId}&action=${action}`),
  previewUndo: () => post<PreviewUndoResponse>('/api/preview/undo'),
  previewFinish: () => post<{ ok: boolean; pool: number }>('/api/preview/finish'),
  addInfo: () => get<{ model_name: string; fields: string[] }>('/api/card/add/info'),
  addCard: (fields: Record<string, string>, tags: string[]) =>
    post<{ noteId: number; cardIds: number[]; pool: number | null }>('/api/card/add', { fields, tags }),
  deleteCard: (cardId: number) =>
    post<{ deleted: boolean }>(`/api/card/delete?card_id=${cardId}`),
  toPreview: (cardId: number) =>
    post<{ moved: boolean; pool: number }>(`/api/card/to-preview?card_id=${cardId}`),
  upload: (file: File): Promise<{ filename: string }> => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch('/api/media/upload', { method: 'POST', body: fd }).then(r =>
      parse<{ filename: string }>(r),
    )
  },
}
