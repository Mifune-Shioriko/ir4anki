import type {
  AnswerResponse,
  MoreResponse,
  NoteResponse,
  NoteUpdateResponse,
  ReadingActResponse,
  ReadingCorpusFile,
  ReadingCreatedCard,
  ReadingEditResponse,
  ReadingSource,
  ReadingSplitResponse,
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
  updateNote: (cardId: number, fields: Record<string, string>, tags: string[]) =>
    post<NoteUpdateResponse>('/api/note/update', { card_id: cardId, fields, tags }),
  // NOTE: the preview-round endpoints (previewStart/Act/Undo/Finish) and
  // toPreview were deleted 2026-09-27 — the preview stage is retired, the
  // backend auto-releases pool cards the next day.
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
  /** Exact card→source provenance (round 4). 404 = the card has no reading
   *  source (pre-r4 / desktop-Anki / orphaned) → null, NOT an error. */
  readingSource: (noteId: number): Promise<ReadingSource | null> =>
    fetch(`/api/reading/source?note_id=${noteId}`).then(r =>
      r.status === 404 ? null : parse<ReadingSource>(r),
    ),
  readingCards: (noteIds: number[]) =>
    get<{ cards: ReadingCreatedCard[]; degraded?: boolean }>(
      `/api/reading/cards?notes=${noteIds.join(',')}`,
    ),
  /** 分割文段 (round 4 + gap_policy 2026-09-22): selected line ranges
   *  become todo children; gaps follow the policy — `bookmark` (default,
   *  进度声明): the tail after the last selection stays todo (未读, keeps
   *  queueing), other gaps → background; `extract` (提炼宣言): ALL gaps →
   *  background. The parent becomes a container. selections = 1-based
   *  inclusive line ranges inside the file. */
  readingSplit: (
    path: string,
    segId: number,
    selections: { start_line: number; end_line: number }[],
    gapPolicy: 'bookmark' | 'extract' = 'bookmark',
  ) => post<ReadingSplitResponse>('/api/reading/split', {
    path, seg_id: segId, selections, gap_policy: gapPolicy,
  }),
  /** In-app segment edit (user spec 2026-09-23): replace the segment's
   *  line range in the .md; backend re-anchors all segments in the same
   *  transaction (app = only writer, no drift fuse). Returns the fresh
   *  chunk payload for in-place UI replacement. */
  readingEdit: (path: string, segId: number, newText: string) =>
    post<ReadingEditResponse>('/api/reading/edit', {
      path, seg_id: segId, new_text: newText,
    }),
  /** Reading-mode note image upload (P5): stores in NOTES_DIR/_assets,
   *  returns the bare filename to embed as `![](filename)`. */
  readingMediaUpload: (file: File): Promise<{ filename: string }> => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch('/api/reading/media/upload', { method: 'POST', body: fd }).then(r =>
      parse<{ filename: string }>(r),
    )
  },
  readingListAdd: (path: string, fresh = false) =>
    post<{ ok: boolean; order: string[]; segments?: number; fresh?: boolean }>(
      '/api/reading/list/add', { path, ...(fresh ? { fresh: true } : {}) }),
  readingListRemove: (path: string) =>
    post<{ ok: boolean; order: string[] }>('/api/reading/list/remove', { path }),
  readingListReorder: (arg: { path: string; top?: boolean } | { order: string[] }) =>
    post<{ ok: boolean; order: string[] }>('/api/reading/list/reorder', arg),
}
