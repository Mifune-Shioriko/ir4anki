import type {
  AnswerResponse,
  MoreResponse,
  NoteResponse,
  NoteSectionsResponse,
  NoteUpdateResponse,
  PreviewActResponse,
  PreviewStartResponse,
  PreviewUndoResponse,
  ReadingActResponse,
  ReadingCorpusFile,
  ReadingCreatedCard,
  ReadingStartResponse,
  ReadingStateResponse,
  ReadingStatusResponse,
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
  start: (mode?: string) =>
    post<StartResponse>(mode ? `/api/session/start?mode=${mode}` : '/api/session/start'),
  more: () => post<MoreResponse>('/api/session/more'),
  finish: () => post<{ synced: boolean }>('/api/session/finish'),
  answer: (cardId: number, ease: number) =>
    post<AnswerResponse>(`/api/answer?card_id=${cardId}&ease=${ease}`),
  undo: () => post<UndoResponse>('/api/undo'),
  note: (cardId: number) => get<NoteResponse>(`/api/note?card_id=${cardId}`),
  tags: () => get<{ tags: string[] }>('/api/tags'),
  noteSections: (noteId: number, topK = 3) =>
    get<NoteSectionsResponse>(`/api/note/sections?note_id=${noteId}&top_k=${topK}`),
  notesRaw: (path: string) =>
    get<{ path: string; text: string }>(`/api/notes/raw?path=${encodeURIComponent(path)}`),
  updateNote: (cardId: number, fields: Record<string, string>, tags: string[]) =>
    post<NoteUpdateResponse>('/api/note/update', { card_id: cardId, fields, tags }),
  previewStart: (mode?: string) =>
    post<PreviewStartResponse>(mode ? `/api/preview/start?mode=${mode}` : '/api/preview/start'),
  previewAct: (cardId: number, action: 'approve' | 'defer') =>
    post<PreviewActResponse>(`/api/preview/act?card_id=${cardId}&action=${action}`),
  previewUndo: () => post<PreviewUndoResponse>('/api/preview/undo'),
  previewFinish: () => post<{ ok: boolean; pool: number }>('/api/preview/finish'),
  addInfo: (kind?: 'qa' | 'cloze') =>
    get<{ model_name: string; fields: string[]; kind?: string }>(
      kind === 'cloze' ? '/api/card/add/info?kind=cloze' : '/api/card/add/info',
    ),
  addCard: (
    fields: Record<string, string>,
    tags: string[],
    readingSource?: { path: string; chunk_key: string } | null,
    kind?: 'qa' | 'cloze',
  ) =>
    post<{ noteId: number; cardIds: number[]; pool: number | null }>('/api/card/add', {
      fields,
      tags,
      ...(kind === 'cloze' ? { kind: 'cloze' } : {}),
      ...(readingSource ? { reading_source: readingSource } : {}),
    }),
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
  // ---- file browser (文件 section, user spec 2026-09-19 round 3) ----
  filesList: () => get<{ files: { path: string; title: string }[] }>('/api/files/list'),
  filesRaw: (path: string) =>
    get<{ path: string; text: string }>(`/api/files/raw?path=${encodeURIComponent(path)}`),
  // ---- reading mode (渐进制卡, 2026-09-19) ----
  readingStatus: () => get<ReadingStatusResponse>('/api/reading/status'),
  readingState: () => get<ReadingStateResponse>('/api/reading/state'),
  readingCorpus: () => get<{ files: ReadingCorpusFile[] }>('/api/reading/corpus'),
  readingFile: (path: string) =>
    get<{ path: string; text: string }>(`/api/reading/file?path=${encodeURIComponent(path)}`),
  readingStart: (mode?: string) =>
    post<ReadingStartResponse>(mode ? `/api/reading/start?mode=${mode}` : '/api/reading/start'),
  readingAct: (path: string, chunkKey: string, action: string) =>
    post<ReadingActResponse>(
      `/api/reading/act?path=${encodeURIComponent(path)}` +
        `&chunk_key=${encodeURIComponent(chunkKey)}&action=${action}`,
    ),
  readingFinish: () => post<{ ok: boolean; stats?: object }>('/api/reading/finish'),
  readingCards: (noteIds: number[]) =>
    get<{ cards: ReadingCreatedCard[]; degraded?: boolean }>(
      `/api/reading/cards?notes=${noteIds.join(',')}`,
    ),
  readingListAdd: (path: string) =>
    post<{ ok: boolean; order: string[] }>('/api/reading/list/add', { path }),
  readingListRemove: (path: string) =>
    post<{ ok: boolean; order: string[] }>('/api/reading/list/remove', { path }),
  readingListReorder: (arg: { path: string; top?: boolean } | { order: string[] }) =>
    post<{ ok: boolean; order: string[] }>('/api/reading/list/reorder', arg),
}
