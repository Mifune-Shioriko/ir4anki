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

/** /api/files/upload rejected with 409: .md name conflicts. The detail is
 *  a JSON list of clashing relative paths — the UI offers 覆盖/改名/跳过
 *  and retries with the chosen policy. */
export class UploadConflictError extends Error {
  conflicts: string[]
  constructor(detail: string) {
    super('文件名冲突')
    this.name = 'UploadConflictError'
    let c: string[] = []
    try {
      const d = JSON.parse(detail) as { conflicts?: string[] }
      if (Array.isArray(d?.conflicts)) c = d.conflicts
    } catch { /* detail wasn't JSON — keep [] */ }
    this.conflicts = c
  }
}

export interface FilesUploadResponse {
  ok: boolean
  saved: { path: string; action: string; original?: string }[]
  images_renamed: Record<string, string>
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

// Admin authorization lives only in memory, never in localStorage or URLs.
let engineToken = ''
export function setEngineToken(value: string) { engineToken = value }
async function engineRequest<T>(url: string, body?: unknown): Promise<T> {
  const headers: Record<string,string> = {}
  if (engineToken) headers.Authorization = `Bearer ${engineToken}`
  const init: RequestInit = { headers }
  if (body !== undefined) { init.method = 'POST'; headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body) }
  return parse<T>(await fetch(url, init))
}

export const api = {
  backendStatus: () => get<{ anki_backend?: string }>('/api/status'),
  engineStats: () => engineRequest<Record<string, unknown>>('/api/engine/stats'),
  engineStatus: () => engineRequest<import('./types').EngineStatus>('/api/engine/status'),
  engineBackup: () => engineRequest<{ handle?: unknown; [key: string]: unknown }>('/api/engine/backup', {}),
  engineExport: () => engineRequest<{ download_url: string }>('/api/engine/export', {}),
  engineSync: () => engineRequest<Record<string, unknown>>('/api/engine/sync', { commit_undo: true }),
  engineDownload: async (path: string): Promise<Blob> => {
    const safe = engineDownloadPath(path, location.origin)
    const headers: Record<string,string> = engineToken ? {Authorization: `Bearer ${engineToken}`} : {}
    const r = await fetch(safe, {headers})
    if (!r.ok) { await parse<unknown>(r); throw new Error('下载失败') }
    if (!r.headers.get('Content-Type')?.startsWith('application/octet-stream')) throw new Error('下载响应不是集合文件')
    return r.blob()
  },
  flowState: () => get<SessionStateResponse>('/api/flow/state'),
  flowStart: (mode?: string) => post<SessionStateResponse>('/api/flow/start' + (mode ? '?mode=' + encodeURIComponent(mode) : '')),
  flowEndReading: () => post<SessionStateResponse>('/api/flow/end-reading'),
  flowExit: () => post<{ ok: boolean }>('/api/flow/exit'),
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
  filesList: () => get<{ files: { path: string; title: string }[]; dirs?: string[] }>('/api/files/list'),
  filesRaw: (path: string) =>
    get<{ path: string; text: string }>(`/api/files/raw?path=${encodeURIComponent(path)}`),
  // ---- file management (user spec 2026-10-06: 前端 = 语料唯一可信来源) ----
  /** Upload .md notes + images. `entries` carry a File and its relative
   *  path (webkitRelativePath for folder uploads, file.name for singles);
   *  they land under `dir` keeping the subtree. A .md name conflict with
   *  onConflict='error' (default) rejects with UploadConflictError listing
   *  the clashing relative paths, so the UI can offer 覆盖/改名/跳过 and
   *  retry. Image basename collisions auto-prefix (no error). */
  filesUpload: (
    entries: { file: File; rel: string }[],
    dir: string,
    onConflict: 'error' | 'overwrite' | 'rename' | 'skip' = 'error',
  ): Promise<FilesUploadResponse> => {
    const fd = new FormData()
    for (const e of entries) {
      fd.append('files', e.file)
      fd.append('rel_paths', e.rel)
    }
    fd.append('dir', dir)
    fd.append('on_conflict', onConflict)
    return fetch('/api/files/upload', { method: 'POST', body: fd }).then(async r => {
      if (r.status === 409) {
        let detail = ''
        try {
          detail = String((JSON.parse(await r.text()) as { detail?: string }).detail ?? '')
        } catch { /* non-JSON detail */ }
        throw new UploadConflictError(detail)
      }
      return parse<FilesUploadResponse>(r)
    })
  },
  filesRename: (path: string, newName: string) =>
    post<{ ok: boolean; path: string; old_path?: string; unchanged?: boolean }>(
      '/api/files/rename', { path, new_name: newName }),
  filesMove: (path: string, newDir: string) =>
    post<{ ok: boolean; path: string; old_path?: string; unchanged?: boolean }>(
      '/api/files/move', { path, new_dir: newDir }),
  filesDelete: (path: string, confirm: string) =>
    post<{ ok: boolean; deleted: string; was_dir: boolean }>(
      '/api/files/delete', { path, confirm }),
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
  /** Split selected file line ranges into todo children. Prefix/middle
   * gaps become background; worthwhile unread tails wait for future rounds.
   * The parent becomes a container; the source file is untouched. */
  readingSplit: (
    path: string,
    segId: number,
    selections: { start_line: number; end_line: number }[],
    guard?: { fingerprint?: string; line_start?: number } | null,
  ) => post<ReadingSplitResponse>('/api/reading/split', {
    path, seg_id: segId, selections,
    ...(guard?.fingerprint != null ? { fingerprint: guard.fingerprint } : {}),
    ...(guard?.line_start != null ? { line_start: guard.line_start } : {}),
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

/** Accept only same-origin engine download routes; never navigate to a
 * backend-provided arbitrary scheme or external host. */
export function engineDownloadPath(value: string, origin: string): string {
  const url = new URL(value, origin)
  if (url.origin !== origin || !url.pathname.startsWith('/api/engine/') || url.username || url.password) {
    throw new Error('导出下载地址无效')
  }
  return url.pathname + url.search
}
