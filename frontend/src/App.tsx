import { Component, Show, createEffect, createSignal, onMount, onCleanup } from 'solid-js'
import { api } from './api'
import type { Card } from './types'
import { syncThemeColor } from './theme'
import { stripTemplateBlocks } from './lib/clean'
import { wrapSelectionAsCloze } from './lib/cloze'

import { NavRail } from './components/NavRail'
import type { Section } from './components/NavRail'
import { FilesScreen } from './components/FilesScreen'
import { Flashcard } from './components/Flashcard'
import { ActionArea } from './components/ActionArea'
import { StartScreen } from './components/StartScreen'
import { EditDialog } from './components/EditDialog'
import { ClozeDialog } from './components/ClozeDialog'
import { ConfirmDialog } from './components/ConfirmDialog'
import { Loading } from './components/Loading'
import { ReadingCard } from './components/ReadingCard'
import { ReadingPanel } from './components/ReadingPanel'
import { ReadingListScreen } from './components/ReadingListScreen'
import { NotePanel } from './components/NotePanel'
import { RelatedPanel } from './components/RelatedPanel'
import { SegEditDialog } from './components/SegEditDialog'
import { invalidateFileCache } from './components/FileViewer'
import { Snackbar } from './components/Snackbar'
import type { StudyModes, ReadingChunk, ReadingChunkStatus, ReadingSource } from './types'
import type { SplitSelection } from './lib/split-selection'

// The note panel (知识成体系 Phase 1) only makes sense on a wide screen —
// the user reviews from phone AND desktop, and on a phone the second column
// would crush the card. A matchMedia signal (not CSS alone) also stops the
// sections fetch from firing on narrow viewports.
const WIDE_QUERY = '(min-width: 1180px)'
// 三栏重构 (user spec 2026-09-21): the LEFT column (相关卡片 = same-segment
// cards, exact provenance) needs a third column. The original spec kept
// every column at the FULL card width (680px → 2216px breakpoint), but the
// user's display is 1920×1200 (report 2026-09-22) and 3×680 doesn't fit.
// Lowered to 1500px: above it the three columns share the viewport as equal
// flexible thirds (CSS .columns--three is flex 1 1 0, capped at 680px) —
// ≈581px each on a 1920 screen. Below: 1180–1499 = 中+右 two columns,
// <1180 = phone single column.
const WIDE3_QUERY = '(min-width: 1500px)'

// Pacing tiers retired (user spec 2026-09-24): one daily flow, no mode
// choice, no persisted selection. (The old MODE_STORAGE_KEY localStorage
// entry is simply ignored; a stale value can't affect anything.)

// 切割语义 persistence (gap_policy, 2026-09-22): bookmark = 进度声明
// (DEFAULT — the cut is a bookmark; the unread tail keeps queueing),
// extract = 提炼宣言 (gaps sink to background). Same localStorage pattern
// as the pacing mode.
const GAP_POLICY_STORAGE_KEY = 'anki-reading-gap-policy'
export type GapPolicy = 'bookmark' | 'extract'
const readStoredGapPolicy = (): GapPolicy => {
  try {
    return localStorage.getItem(GAP_POLICY_STORAGE_KEY) === 'extract'
      ? 'extract'
      : 'bookmark'
  } catch {
    return 'bookmark'
  }
}
const writeStoredGapPolicy = (p: GapPolicy) => {
  try {
    localStorage.setItem(GAP_POLICY_STORAGE_KEY, p)
  } catch {
    /* not critical */
  }
}

type Phase =
  | 'loading'
  | 'start'
  | 'review'
  // 单一流水线 (user spec 2026-09-24; preview stage retired 2026-09-27):
  // 阅读 → 复习 自动链接，中间统计页/阶段开始页（readingStart、
  // readingDone、done、empty、finished）全部退役——轮完成直接进下一段，
  // 复习完成回开始页。预览环节已删除：新卡次日自动放行。
  | 'reading'

export const App: Component = () => {
  createEffect(() => syncThemeColor())

  // ---- left nav rail section (user spec 2026-09-19 round 3) ----
  // The old "Anki" top bar became a left icon rail with three destinations:
  // 学习 (the whole funnel below), 文件 (read-only note browser) and
  // 阅读清单 (list manager — used to be a phase). Section is NOT persisted
  // across reloads: a page load always lands on 学习 so an active round
  // resumes where it left off.
  const [section, setSection] = createSignal<Section>('study')
  // reading-list membership for the 文件 browser's 清单 badges — fetched
  // lazily when the section opens (cheap; the list is small)
  const [listedPaths, setListedPaths] = createSignal<Set<string>>(new Set())
  const refreshListedPaths = async () => {
    if (!readingMode()) {
      setListedPaths(new Set<string>())
      return
    }
    try {
      const st = await api.readingStatus()
      setListedPaths(new Set(st.list.map(s => s.path)))
    } catch { /* badges are decorative */ }
  }
  const navigate = (s: Section) => {
    if (s === section()) return
    setSection(s)
    // returning to the study section re-reads server state (list edits in
    // the 阅读清单 section can change the funnel, e.g. adding files)
    if (s === 'study') resync()
    if (s === 'files') refreshListedPaths()
  }

  // ---- wide-screen detection (note panel gate) ----
  const mq = window.matchMedia(WIDE_QUERY)
  const [isWide, setIsWide] = createSignal(mq.matches)
  const onMq = (e: MediaQueryListEvent) => setIsWide(e.matches)
  const mq3 = window.matchMedia(WIDE3_QUERY)
  const [isWide3, setIsWide3] = createSignal(mq3.matches)
  const onMq3 = (e: MediaQueryListEvent) => setIsWide3(e.matches)
  onMount(() => {
    mq.addEventListener('change', onMq)
    mq3.addEventListener('change', onMq3)
  })
  onCleanup(() => {
    mq.removeEventListener('change', onMq)
    mq3.removeEventListener('change', onMq3)
  })

  // ---- phase & loading text ----
  const [phase, setPhase] = createSignal<Phase>('loading')
  const [loadText, setLoadText] = createSignal('正在加载…')
  // audit item #2 fix: a failed load used to leave the spinner up forever
  // with only the error text — no way out but a hard refresh. loadError
  // swaps the Loading screen for an error card with a retry button.
  const [loadError, setLoadError] = createSignal(false)

  // ---- round state ----
  const [cards, setCards] = createSignal<Card[]>([])
  const [idx, setIdx] = createSignal(0)
  // Reveal state is keyed BY CARD ID, not a bare boolean (2026-09-07 fix):
  // with a boolean, any path where the current card changed without the
  // reset landing first (async race, engine timing quirk, stale resync)
  // rendered the NEW card's question together with a revealed answer zone —
  // the "上一张的正面 + 下一张的背面" bug. With an id key the answer zone
  // can only ever open for the card that was actually revealed; any other
  // card is face-down by construction.
  const [revealedFor, setRevealedFor] = createSignal<number | null>(null)
  const revealed = () => {
    const c = cards()[idx()]
    return !!c && revealedFor() === c.cardId
  }
  const [answering, setAnswering] = createSignal(false)
  // roundTotal is the round's ORIGINAL size (done + pending) — from the
  // server, never cards().length after a mid-round resume.
  const [roundTotal, setRoundTotal] = createSignal(0)
  const [totalDone, setTotalDone] = createSignal(0)

  // ---- global stats ----
  const [due, setDue] = createSignal<number | null>(null)
  const [newTotal, setNewTotal] = createSignal<number | null>(null)

  // ---- pacing (single daily tier, user spec 2026-09-24) ----
  // quick/focus are RETIRED along with every intermediate stats page: one
  // button runs 阅读 → 预览 → 复习 and lands back here. selectedMode stays
  // as a wire-compat constant ("daily"); studyModes = the backend's resolved
  // table (dynamic review size) rendered as 今日每轮计划 on the start screen.
  const [studyModes, setStudyModes] = createSignal<StudyModes | null>(null)
  const selectedMode = () => 'daily'

  // ---- 切割语义 (gap_policy, 2026-09-22) ----
  // bookmark (default) = 进度声明: the cut is a bookmark, the unread tail
  // stays queued; extract = 提炼宣言: gaps sink to background. Shared by the
  // round and trace ReadingCard variants; persisted across reloads.
  const [gapPolicy, setGapPolicy] = createSignal<GapPolicy>(readStoredGapPolicy())
  const chooseGapPolicy = (p: GapPolicy) => {
    setGapPolicy(p)
    writeStoredGapPolicy(p)
  }

  // ---- review/new split for the current batch (front-end only) ----
  // Batch composition is snapshotted at load time; the setters below are
  // maintained by answer/undo/delete bookkeeping. The derived per-split
  // counters died with the DoneScreen (2026-09-24).
  const [, setBatchNewTotal] = createSignal(0)
  const [, setBatchReviewTotal] = createSignal(0)

  // ---- undo ----
  // Availability mirrors the backend's single undo slot (round.json "last"):
  // on after each answer, off once undone, no time limit. Initial value is
  // restored from /api/session/state on page load.
  const [canUndo, setCanUndo] = createSignal(false)
  const [undoBusy, setUndoBusy] = createSignal(false)

  // ---- edit dialog ----
  const [editOpen, setEditOpen] = createSignal(false)

  // ---- add card + delete (user spec 2026-09-04; to-preview deleted
  // 2026-09-27 together with the preview stage) ----
  // The add dialog reuses EditDialog in mode='add' (same rich editor, same
  // bottom-sheet style); the new card lands in the preview pool suspended
  // and auto-releases the next day. Delete asks for confirmation (防呆).
  const [addOpen, setAddOpen] = createSignal(false)
  const [deleteTarget, setDeleteTarget] = createSignal<{ cardId: number; preview: string } | null>(null)
  const [deleteBusy, setDeleteBusy] = createSignal(false)

  // ---- preview pool (先看后考 → 次日自动放行, 2026-09-27) ----
  // The manual preview STAGE (round UI, approve/defer, undo, 移回预览池) is
  // gone; what remains is the pool itself: cards made today sit there
  // suspended and the backend auto-releases them the next Anki day. The
  // chip counts on the start screen explain why today's cards are not in
  // the new pool yet. Gated entirely by the backend flag (resync reads it).
  const [previewMode, setPreviewMode] = createSignal(false)
  const [previewPool, setPreviewPool] = createSignal<number | null>(null)
  // cards made TODAY still in the pool (auto-release tomorrow)
  const [pendingRelease, setPendingRelease] = createSignal<number | null>(null)
  // daily auto-release cap + remaining slots (wire fields; null = disabled
  // or old backend). Setters tracked in resync; getters unused for now (the
  // start screen's pool row doesn't render the cap — keep the signals so a
  // future UI can surface 剩余放行 without a wire change).
  const [, setReleaseDailyGoal] = createSignal<number | null>(null)
  const [, setReleaseBudgetLeft] = createSignal<number | null>(null)

  // ---- reading mode (渐进制卡, user spec 2026-09-19) ----
  // Gated by the backend flag like preview. Pipeline (2026-09-24): reading
  // → preview → review, chained automatically. The round deals frontier
  // chunks (priority = 阅读清单 order); `active` chunks resurface first
  // until the user marks them done/skipped. addSource links the add-card
  // dialog to the current chunk (provenance → cards_created).
  const [readingMode, setReadingMode] = createSignal(false)
  const [readingListSize, setReadingListSize] = createSignal(0)
  const [readingAvailable, setReadingAvailable] = createSignal(0)
  const [readingActive, setReadingActive] = createSignal(0)
  const [, setReadingGated] = createSignal(0)
  const [rdChunks, setRdChunks] = createSignal<ReadingChunk[]>([])
  const [rdDone, setRdDone] = createSignal(0)
  const [rdTotal, setRdTotal] = createSignal(0)
  const [, setRdStats] = createSignal({})
  const [rdBusy, setRdBusy] = createSignal(false)
  // which chunk the add dialog is open FOR (provenance link); null = plain add
  const [addSource, setAddSource] = createSignal<{ path: string; chunk_key: string } | null>(null)
  const rdCurrent = () => rdChunks()[0] ?? null
  // 挖空卡 dialog (user spec 2026-09-19 round 2): clozeInitial is the
  // textarea seed — the chunk-body selection wrapped in {{c1::}} when the
  // user selected text first, else the whole chunk text.
  const [clozeOpen, setClozeOpen] = createSignal(false)
  const [clozeInitial, setClozeInitial] = createSignal('')

  // ---- 溯源 (trace detour, 三栏重构 user spec 2026-09-21) ----
  // A review/preview card's 溯源 button jumps OUT of the round into a
  // temporary reading session on the card's source segment: 中栏 = the
  // chunk (trace variant, no round actions), 左栏 = 相关卡片 of that
  // segment, 右栏 = whole file anchored at the segment. The underlying
  // round phase is untouched — 回到复习 (icon, top-right of the chunk card
  // or Escape) just closes the detour and the suspended card returns in
  // its exact state (face-up preserved via revealedFor keys).
  const srcCache = new Map<number, ReadingSource | null>() // noteId → source; null = orphan (404)
  const [traceSource, setTraceSource] = createSignal<ReadingSource | null>(null)
  const [traceOpen, setTraceOpen] = createSignal(false)
  const [traceChunk, setTraceChunk] = createSignal<ReadingChunk | null>(null)
  // bump to force the left 相关卡片 panel to refetch (after adds/splits)
  const [relatedToken, setRelatedToken] = createSignal(0)
  // bump to force the RIGHT 原文上下文 FileViewer to re-fetch the .md after
  // an in-app segment edit rewrote it (path unchanged → cache+effect would
  // otherwise serve stale text). Paired with invalidateFileCache().
  const [noteReloadToken, setNoteReloadToken] = createSignal(0)

  const sourceToChunk = (s: ReadingSource): ReadingChunk => {
    const own = (s.cards_created || []).filter(id => id > 0)
    const anc: number[] = []
    for (const b of s.breadcrumb) {
      for (const id of b.cards_created || []) {
        if (id > 0 && !own.includes(id) && !anc.includes(id)) anc.push(id)
      }
    }
    const file = s.path.replace(/^\d{4}\//, '').replace(/\.md$/, '')
    const last = s.breadcrumb[s.breadcrumb.length - 1]
    return {
      path: s.path,
      chunk_key: String(s.seg_id),
      seg_id: s.seg_id,
      parent_seg_id: null,
      title: (last && last.title) || file,
      heading_path: s.breadcrumb.map(b => b.title).filter(t => !!t),
      line_start: s.line_start,
      line_end: s.line_end,
      text: s.text,
      status: (s.status as ReadingChunkStatus) || 'todo',
      cards_created: own,
      ancestor_cards: anc,
      file_chunks: 0,
      file_done: 0,
      file_skipped: 0,
    }
  }

  const openTrace = () => {
    const s = traceSource()
    if (!s || traceOpen()) return
    setTraceChunk(sourceToChunk(s))
    setTraceOpen(true)
  }
  const closeTrace = () => {
    setTraceOpen(false)
    setTraceChunk(null)
  }

  // the chunk the add/cloze/split actions target: the trace detour's chunk
  // when open, else the reading round's current chunk
  const activeChunk = () => (traceOpen() ? traceChunk() : rdCurrent())

  // 分割文段 (round-4 split): selection lines are RELATIVE to the chunk
  // text → shift by line_start-1 for file lines. Selected range → todo
  // child (a smaller reading card), gaps → background, parent → container.
  // In-round: children splice into rdChunks at the parent's position (the
  // backend mirrors this in round.pending) and rdTotal grows. Trace detour:
  // the detour lands on the first todo child; the round (if any) is
  // re-fetched afterwards because the traced segment may have been pending
  // in it (the backend replaced it there too).
  // child_chunks (full payloads) is the r4.5 wire field; if absent (old
  // backend process) the todo children are built CLIENT-SIDE from the
  // returned line ranges + the file text — the split itself succeeded
  // server-side either way, the UI must not claim otherwise.
  const buildFallbackChildren = async (
    chunk: ReadingChunk,
    children: { seg_id: number; start_line: number; end_line: number; status: string; tail?: boolean }[],
  ): Promise<ReadingChunk[]> => {
    // bookmark 的未读尾段不进屏（child_chunks 已排除它）——fallback 同样排除
    const todos = children.filter(c => c.status === 'todo' && !c.tail)
    if (todos.length === 0) return []
    try {
      const f = await api.readingFile(chunk.path)
      const lines = f.text.split('\n')
      return todos.map(c => ({
        ...chunk,
        chunk_key: String(c.seg_id),
        seg_id: c.seg_id,
        parent_seg_id: chunk.seg_id,
        line_start: c.start_line,
        line_end: c.end_line,
        text: lines.slice(c.start_line - 1, c.end_line).join('\n'),
        status: 'todo' as ReadingChunkStatus,
        cards_created: [],
        // pre-split cards live on the (now container) parent — carry them as
        // ancestors so the left column keeps showing the duplicate guard
        ancestor_cards: [...(chunk.cards_created || []), ...(chunk.ancestor_cards || [])],
      }))
    } catch {
      return []
    }
  }

  const doSplit = async (chunk: ReadingChunk, sel: SplitSelection) => {
    if (chunk.seg_id == null || rdBusy()) return
    const base = chunk.line_start - 1
    const selections = [{ start_line: base + sel.start_line, end_line: base + sel.end_line }]
    const wasTrace = traceOpen()
    const policy = gapPolicy()
    setRdBusy(true)
    try {
      const res = await api.readingSplit(chunk.path, chunk.seg_id, selections, policy)
      let kids = res.child_chunks || []
      if (kids.length === 0) kids = await buildFallbackChildren(chunk, res.children || [])
      if (kids.length === 0) {
        showSnack('分割失败：没有产生新片段')
        return
      }
      if (wasTrace) {
        setTraceChunk(kids[0])
        // the traced segment may have sat in the active reading round's
        // pending list — the backend swapped it for the children there, so
        // rebuild the local mirror (cheap; silent on failure)
        try {
          const st = await api.readingState()
          if (st.round?.status === 'active') {
            setRdChunks(st.round.chunks ?? [])
            setRdDone(st.round.done)
            setRdTotal(st.round.total)
          }
        } catch { /* next resync fixes it */ }
      } else {
        setRdChunks(prev => {
          const i = prev.findIndex(
            c => c.chunk_key === chunk.chunk_key && c.path === chunk.path,
          )
          if (i < 0) return prev
          const next = [...prev]
          next.splice(i, 1, ...kids)
          return next
        })
        setRdTotal(t => t + kids.length - 1)
      }
      setRelatedToken(t => t + 1)
      const hasTail = (res.children || []).some(c => c.tail && c.status === 'todo')
      if (policy === 'bookmark' && hasTail) {
        showSnack(
          `已分割出 ${kids.length} 个片段；未读的尾巴留在队列，消化完卡片后会自动推回来`,
        )
      } else {
        showSnack(
          kids.length === 1
            ? '已分割出 1 个新片段（未选中部分转入背景，可在阅读清单提升）'
            : `已分割出 ${kids.length} 个新片段（未选中部分转入背景，可在阅读清单提升）`,
        )
      }
    } catch (e) {
      showSnack('分割失败：' + (e as Error).message)
    } finally {
      setRdBusy(false)
    }
  }

  // ---- 片段编辑 (user spec 2026-09-23) ----
  // SegEditDialog edits the chunk's .md SOURCE via POST /api/reading/edit
  // (backend re-anchors all segments in one transaction — app is the only
  // writer, no drift fuse). On save: replace the on-screen chunk (round OR
  // trace variant) with the fresh payload, bust the FileViewer cache so the
  // right column re-renders the new file text, and nudge the left 相关卡片
  // panel (line numbers behind provenance moved).
  const [segEditChunk, setSegEditChunk] = createSignal<ReadingChunk | null>(null)
  const segEditOpen = () => segEditChunk() !== null
  const openSegEdit = (chunk: ReadingChunk) => setSegEditChunk(chunk)

  const onSegEdited = (fresh: ReadingChunk | null) => {
    const old = segEditChunk()
    setSegEditChunk(null)
    if (!old) return
    if (fresh) {
      if (traceOpen() && traceChunk()?.chunk_key === old.chunk_key) {
        setTraceChunk(fresh)
      } else {
        setRdChunks(prev =>
          prev.map(c =>
            c.path === old.path && c.chunk_key === old.chunk_key ? fresh : c,
          ),
        )
      }
    }
    // the .md changed on disk → the right column must re-fetch the file
    invalidateFileCache(old.path)
    setNoteReloadToken(t => t + 1)
    setRelatedToken(t => t + 1)
    showSnack('片段已保存，笔记文件已更新')
  }

  // ---- MD3 snackbar (transient feedback, 2026-09-16) ----
  // Currently used by the double-Again auto-return: the card silently left
  // the round, so the user needs to know WHY. Auto-dismiss ~4s per the MD3
  // snackbar guidance (short label, no action needed).
  const [snackText, setSnackText] = createSignal<string | null>(null)
  let snackTimer: ReturnType<typeof setTimeout> | null = null
  const showSnack = (text: string) => {
    if (snackTimer) clearTimeout(snackTimer)
    setSnackText(text)
    snackTimer = setTimeout(() => setSnackText(null), 4000)
  }
  onCleanup(() => { if (snackTimer) clearTimeout(snackTimer) })

  const adoptGlobal = (d: {
    due_remaining?: number | null
    new_total?: number | null
  }) => {
    if (d.due_remaining != null) setDue(d.due_remaining)
    if (d.new_total != null) setNewTotal(d.new_total)
  }

  const loadBatch = (list: Card[], done: number, total: number, batchNew?: number) => {
    setCards(list)
    // Prefer the backend's authoritative batch composition (round.json
    // new_count). Fall back to counting the list only when the backend
    // didn't record it — valid for live start/more (list IS the full batch)
    // but wrong for mid-round resume (list is only what's left).
    const nNew = batchNew ?? list.filter(c => c.isNew).length
    setBatchNewTotal(nNew)
    setBatchReviewTotal(total - nNew)
    setIdx(0)
    setTotalDone(done)
    setRoundTotal(total)
    setRevealedFor(null)
    setPhase('review')
  }

  // ---- resync from the backend (self-heal after any state drift) ----
  const resync = async () => {
    try {
      const d = await api.sessionState()
      adoptGlobal(d)
      setCanUndo(d.can_undo)
      // pacing-mode table from the wire (2026-09-14); validate the stored
      // selection against it so a renamed/removed tier can't strand the UI
      if (d.study_modes) {
        setStudyModes(d.study_modes)
      }
      // preview-pool signals (absent when the backend flag is off). The
      // manual preview STAGE is gone (2026-09-27): only the pool counters
      // survive, to explain why today's cards aren't in the new pool yet.
      setPreviewMode(!!d.preview_mode)
      if (d.preview_mode) {
        setPreviewPool(d.preview_pool ?? null)
        setPendingRelease(d.pending_release ?? null)
        setReleaseDailyGoal(d.release_daily_goal ?? null)
        setReleaseBudgetLeft(d.release_budget_left ?? null)
      }
      // reading-mode signals (absent when the backend flag is off)
      setReadingMode(!!d.reading_mode)
      if (d.reading_mode) {
        setReadingListSize(d.reading_list_size ?? 0)
        setReadingAvailable(d.reading_available ?? 0)
        setReadingActive(d.reading_active ?? 0)
        setReadingGated(d.reading_gated ?? 0)
      }
      // 单一流水线 (2026-09-24; preview stage retired 2026-09-27): a page
      // load resumes an ACTIVE round where it left off; an active reading
      // round resumes too; anything else lands on the single start screen.
      if (d.state === 'active') {
        loadBatch(d.cards, d.done, d.total, d.new_in_batch ?? undefined)
      } else if (d.reading_mode && d.reading_round?.status === 'active') {
        setRdChunks(d.reading_round.chunks ?? [])
        setRdDone(d.reading_round.done ?? 0)
        setRdTotal(d.reading_round.total ?? 0)
        setRdStats(d.reading_round.stats ?? {})
        setPhase('reading')
      } else {
        setPhase('start')
      }
    } catch {
      /* keep current view; next interaction will retry */
    }
  }

  // ---- init: resume an in-progress round or show the start screen ----
  createEffect(() => { resync() })

  // ---- 单一流水线 (user spec 2026-09-24; preview stage retired 2026-09-27) ----
  // 开始 → 阅读(4段) → 复习(15新+ceil(D/3)到期) → 回开始页。
  // 每段完成自动进入下一段；某段无内容（清单读完/无卡）直接跳到再下一段。
  // 预览环节已删除：今天制的卡进预览池，后端次日自动放行成新卡。
  const startReadingStage = async () => {
    setPhase('loading')
    setLoadError(false)
    setLoadText('正在加载阅读片段…')
    setRdBusy(true)
    try {
      const d = await api.readingStart(selectedMode())
      if (d.study_modes) setStudyModes(d.study_modes)
      if (!d.chunks.length) {
        if (d.all_gated) {
          showSnack('正在制卡的片段都在等卡片过预览池——明天它们会重新推送')
        }
        await startReviewStage()
        return
      }
      setRdChunks(d.chunks)
      setRdDone(0)
      setRdTotal(d.chunks.length)
      setRdStats({})
      setPhase('reading')
    } catch (e) {
      setLoadText('加载失败：' + (e as Error).message)
      setLoadError(true)
    } finally {
      setRdBusy(false)
    }
  }

  const startReviewStage = async () => {
    setPhase('loading')
    setLoadError(false)
    setLoadText('正在同步并加载卡片…')
    try {
      const data = await api.start(selectedMode())
      adoptGlobal(data)
      if (data.study_modes) setStudyModes(data.study_modes)
      if (!data.cards.length) {
        // nothing left anywhere — the chain ends here
        await api.finish().catch(() => {})
        setPhase('start')
        showSnack('本轮完成 🎉 没有更多卡片了')
        await resync()
        return
      }
      setCanUndo(false) // a fresh round starts with no undo slot
      loadBatch(data.cards, 0, data.cards.length)
    } catch (e) {
      setLoadText('加载失败：' + (e as Error).message)
      setLoadError(true)
    }
  }

  // the single 开始 button: first stage with content wins
  const beginFlow = async () => {
    if (readingMode() && ((readingAvailable() > 0) || (readingActive() > 0))) {
      await startReadingStage()
    } else {
      await startReviewStage()
    }
  }

  // stage transitions (each persists its round server-side before chaining)
  const chainAfterReading = async () => {
    await api.readingFinish().catch(() => {})
    rdRefreshCounts()
    await startReviewStage()
  }
  const chainAfterReview = async () => {
    // local batch is already drained; clear it and sync the day's progress
    setCards([])
    setIdx(0)
    setRevealedFor(null)
    setCanUndo(false)
    setPhase('loading')
    setLoadText('正在同步…')
    await api.finish().catch(() => {})
    setPhase('start')
    showSnack('本轮完成 🎉')
    await resync()
  }

  const answer = async (ease: number) => {
    const card = cards()[idx()]
    if (!card || answering()) return
    setAnswering(true)
    try {
      const data = await api.answer(card.cardId, ease)
      if (!data.answered) {
        // backend rejected it (card no longer in this round — e.g. answered
        // on another device). Resync to the server's view instead of
        // advancing on stale local state.
        await resync()
        return
      }

      // Remove the answered card BY ID — the local array mirrors the
      // backend's pending list 1:1, so undo positions (backend 'index')
      // line up. Position-based removal could drop the wrong card if idx
      // drifted while the request was in flight.
      setCards(prev => prev.filter(c => c.cardId !== card.cardId))
      setTotalDone(totalDone() + 1)
      // idx now naturally points at the next card (or past the end)

      // backend now holds an undo slot for this answer — no time limit
      setCanUndo(true)

      const roundDone = data.round != null && data.round.state === 'complete'
      if (!roundDone && idx() < cards().length) {
        setRevealedFor(null)
      } else {
        if (data.round) adoptGlobal(data.round)
        // 复习是流水线最后一段 (2026-09-24): no stats page — straight back
        // to the start screen with a completion snackbar
        await chainAfterReview()
      }
    } catch (e) {
      alert('评分失败：' + (e as Error).message)
    } finally {
      setAnswering(false)
    }
  }

  const undo = async () => {
    if (undoBusy() || answering()) return
    setUndoBusy(true)
    try {
      const d = await api.undo()
      setCards(prev => {
        const next = [...prev]
        // defensive: never insert a duplicate if the card somehow stayed
        next.splice(Math.min(d.index, next.length), 0, d.card)
        return next
      })
      setIdx(Math.min(d.index, cards().length - 1))
      setTotalDone(v => Math.max(0, v - 1))
      setRevealedFor(null)
      setCanUndo(false) // the single undo slot is consumed
    } catch (e) {
      alert('撤销失败：' + (e as Error).message)
    } finally {
      setUndoBusy(false)
    }
  }

  // 中途退出复习 had no UI entry since the DoneScreen retirement
  // (2026-09-24) — review rounds resume on reload; the natural end is
  // chainAfterReview. (The old finish() handler lived here.)

  // ---- card lifecycle: add / delete / back to preview pool (user spec
  // 2026-09-04). Add reuses EditDialog in mode='add'; delete and to-preview
  // go through a confirmation dialog first (防呆). The backend already keeps
  // round.json/preview.json consistent (card counts as handled), so the
  // frontend just mirrors that locally — same splice pattern as answer().

  const openAdd = () => {
    // provenance inheritance (r4, backend docstring: 卡 a 复习时制的卡 b
    // 也归到 a 的片段): when the on-screen card has a reading source, the
    // new card joins that segment — it then shows up in the left column's
    // 相关卡片 list and participates in the preview-pool gate like any
    // segment-born card. Orphan card / no source → plain add.
    const s = traceSource()
    setAddSource(s ? { path: s.path, chunk_key: String(s.seg_id) } : null)
    setAddOpen(true)
  }
  // 渐进制卡: add from a chunk — links the new card to the chunk
  // (provenance → cards_created). activeChunk() = the trace detour's chunk
  // when 溯源 is open (cards made while tracing inherit the traced
  // segment), else the reading round's current chunk.
  const openReadingAdd = () => {
    const chunk = activeChunk()
    setAddSource(chunk ? { path: chunk.path, chunk_key: chunk.chunk_key } : null)
    setAddOpen(true)
  }
  // 添加挖空 (user spec 2026-09-19 round 2; CONTEXT FIX 2026-09-20; MARKDOWN
  // PRESERVATION 2026-09-20): the dialog seeds from the WHOLE chunk's RAW
  // MARKDOWN (tables/bold/$math$ stay visible while editing; the field is
  // converted to HTML on save — Anki renders HTML natively, so the card
  // looks exactly like the chunk with a hole punched in it). A selection
  // inside the chunk body is auto-wrapped in {{c1::…}} IN PLACE, keeping the
  // surrounding sentence as recall context. The OLD behavior seeded with
  // ONLY the selection — the card front became a bare "[…]" with nothing to
  // recall from (user report). Selection can't be located (exotic markdown)
  // → whole chunk, user wraps manually. No selection → whole chunk as-is.
  const openReadingCloze = (selText: string, selBlock?: string) => {
    const chunk = activeChunk()
    if (!chunk) return
    const base = chunk.text
    let seed = base
    if (selText) {
      const wrapped = wrapSelectionAsCloze(base, selText, 1, selBlock || undefined)
      if (wrapped !== null) seed = wrapped
    }
    setClozeInitial(seed)
    setAddSource({ path: chunk.path, chunk_key: chunk.chunk_key })
    setClozeOpen(true)
  }

  const onCardAdded = (noteId?: number) => {
    // the new card lands suspended in the preview pool (auto-released
    // tomorrow) — reflect in the pool chip
    if (previewMode()) {
      setPreviewPool(p => (p == null ? p : p + 1))
      setPendingRelease(p => (p == null ? p : p + 1))
    }
    // 渐进制卡: the backend recorded the note id on the source chunk AND
    // auto-marked a todo chunk as active — mirror both locally so 「已从本
    // 片段制卡 N 张」/已制卡片列表 and the 正在制卡 chip update without a
    // reload. Pass the REAL note id so ReadingCard can fetch its content
    // (placeholder 0 would only bump the count).
    const src = addSource()
    if (src) {
      setRdChunks(prev =>
        prev.map(c =>
          c.path === src.path && c.chunk_key === src.chunk_key
            ? {
                ...c,
                cards_created: [...c.cards_created, noteId || 0],
                status: c.status === 'todo' ? 'active' : c.status,
              }
            : c,
        ),
      )
      // 溯源 detour: the chunk card is traceChunk, not rdChunks — mirror the
      // provenance there too so the left 相关卡片 column grows live
      setTraceChunk(prev =>
        prev && prev.path === src.path && prev.chunk_key === src.chunk_key
          ? {
              ...prev,
              cards_created: [...prev.cards_created, noteId || 0],
              status: prev.status === 'todo' ? 'active' : prev.status,
            }
          : prev,
      )
      if (readingMode()) rdRefreshCounts()
    }
    // any add can change the left 相关卡片 column (trace/reading provenance
    // or a future re-resolve) — force the panel to refetch
    setRelatedToken(t => t + 1)
    setAddSource(null)
  }

  const confirmDelete = async () => {
    const t = deleteTarget()
    if (!t || deleteBusy()) return
    setDeleteBusy(true)
    try {
      await api.deleteCard(t.cardId)
      setDeleteTarget(null)
      if (phase() === 'review') {
        const removed = cards().find(c => c.cardId === t.cardId)
        const wasNew = removed?.isNew
        setCards(prev => prev.filter(c => c.cardId !== t.cardId))
        setTotalDone(v => v + 1)
        if (wasNew) setBatchNewTotal(v => Math.max(0, v - 1))
        else setBatchReviewTotal(v => Math.max(0, v - 1))
        if (cards().length === 0) await chainAfterReview()
        else setRevealedFor(null)
      }
    } catch (e) {
      alert('删除失败：' + (e as Error).message)
    } finally {
      setDeleteBusy(false)
    }
  }

  // short plain-text preview of a card's front, for confirmation dialogs.
  // Template blocks (<style>/<script>) are stripped FIRST — otherwise the
  // note type's card CSS shows up as the "preview" text (found via UI test).
  const plainPreview = (html: string, max = 40) => {
    const text = stripTemplateBlocks(html)
      .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
    return text.length > max ? text.slice(0, max) + '…' : text
  }
  const askDelete = (card: Card) =>
    setDeleteTarget({ cardId: card.cardId, preview: plainPreview(card.question) })

  // NOTE: the whole preview-round action block (previewCard/pvAct/pvUndo/
  // pvFinish) and 移回预览池 (confirmToPreview/askToPreview) were deleted
  // 2026-09-27 — the preview stage is retired, releases are automatic.

  // ---- reading actions (渐进制卡, 2026-09-19) ----
  const rdRefreshCounts = async () => {
    // cheap count refresh after round changes (list/available/active)
    try {
      const d = await api.sessionState()
      if (d.reading_mode) {
        setReadingListSize(d.reading_list_size ?? 0)
        setReadingAvailable(d.reading_available ?? 0)
        setReadingActive(d.reading_active ?? 0)
        setReadingGated(d.reading_gated ?? 0)
      }
    } catch { /* counts are decorative; next resync fixes them */ }
  }

  const rdAct = async (action: 'mark_active' | 'complete' | 'skip' | 'next') => {
    const chunk = rdCurrent()
    if (!chunk || rdBusy()) return
    setRdBusy(true)
    try {
      const d = await api.readingAct(chunk.path, chunk.chunk_key, action)
      if (!d.ok) {
        // stale / drifted — resync to the server's view
        await resync()
        return
      }
      if (action === 'mark_active') {
        // stays on this chunk (正在制卡): update its status in place
        setRdChunks(prev =>
          prev.map(c =>
            c.chunk_key === chunk.chunk_key && c.path === chunk.path
              ? { ...c, status: 'active' }
              : c,
          ),
        )
        return
      }
      // complete / skip / next: remove BY chunk_key (never by position —
      // the same rule as the review/preview arrays)
      setRdChunks(prev =>
        prev.filter(c => !(c.chunk_key === chunk.chunk_key && c.path === chunk.path)),
      )
      setRdDone(d.done ?? rdDone() + 1)
      if (d.stats) setRdStats(d.stats)
      if (d.round_complete || rdChunks().length === 0) {
        setRdTotal(d.total ?? rdTotal())
        // 单一流水线 (2026-09-24; preview stage retired 2026-09-27):
        // reading drained → review stage, no readingDone stats page
        await chainAfterReading()
      }
    } catch (e) {
      alert('操作失败：' + (e as Error).message)
    } finally {
      setRdBusy(false)
    }
  }

  // 中途退出阅读 (ReadingCard 结束阅读): park the round (untouched chunks
  // keep their state) and reconcile via resync.
  const rdFinish = async () => {
    setRdBusy(true)
    try {
      await api.readingFinish()
    } catch { /* not critical */ }
    setRdBusy(false)
    setPhase('loading')
    setLoadText('正在同步…')
    await resync()
  }

  // ---- keyboard shortcuts ----
  // Attached at WINDOW level (2026-09-07 fix): the old div-level onKeyDown
  // only fired when focus was already inside the app — on a fresh page load
  // focus sits on <body>, so Space/Enter/1-4/Ctrl+Z were dead until the
  // user clicked something first.
  const isTypingTarget = (t: EventTarget | null) => {
    const el = t as HTMLElement | null
    if (!el || !el.tagName) return false
    return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable === true
  }
  const handleKeyDown = (e: KeyboardEvent) => {
    if (isTypingTarget(e.target)) return
    if (editOpen() || addOpen() || deleteTarget() || segEditOpen()) return
    // 溯源 detour (2026-09-21): the underlying round is SUSPENDED — its
    // shortcuts (Space/1-4/approve) must not fire behind the trace card.
    // Escape = 回到复习; A/C keep working (they target the traced chunk).
    if (traceOpen()) {
      if (clozeOpen()) return
      if (e.key === 'Escape') {
        e.preventDefault()
        closeTrace()
      } else if (e.key === 'a' || e.key === 'A') {
        e.preventDefault()
        openReadingAdd()
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault()
        openReadingCloze('', '')
      }
      return
    }
    if (phase() === 'reading') {
      // any dialog open (add / cloze): Space is typing there, never complete
      if (clozeOpen()) return
      // 2026-09-19 round 2: 开始制卡 is gone — Space = 制卡完成 (primary),
      // A = add card, C = add cloze, S = skip, N = 下一张 (稍后继续)
      if (rdBusy()) return
      const chunk = rdCurrent()
      if (!chunk) return
      if (e.key === ' ') {
        e.preventDefault()
        rdAct('complete')
      } else if (e.key === 'a' || e.key === 'A') {
        e.preventDefault()
        openReadingAdd()
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault()
        // keyboard path has no selection context — seed from the whole chunk
        openReadingCloze('', '')
      } else if (e.key === 's' || e.key === 'S') {
        e.preventDefault()
        rdAct('skip')
      } else if (e.key === 'n' || e.key === 'N') {
        e.preventDefault()
        rdAct('next')
      }
      return
    }
    if (phase() !== 'review') return
    if (e.key === ' ' && !revealed()) {
      e.preventDefault()
      setRevealedFor(currentCard()?.cardId ?? null)
    } else if (revealed() && !answering()) {
      if (e.key === '1') answer(1)
      else if (e.key === '2') answer(2)
      else if (e.key === '3') answer(3)
      else if (e.key === '4') answer(4)
    }
    if (e.key === 'z' && (e.ctrlKey || e.metaKey) && canUndo() && !answering()) {
      e.preventDefault()
      undo()
    }
  }

  // Window-level registration (2026-09-07): works even when focus is on
  // <body> right after page load. The old div-level onKeyDown only fired
  // after the user clicked something inside the app first.
  onMount(() => window.addEventListener('keydown', handleKeyDown))
  onCleanup(() => window.removeEventListener('keydown', handleKeyDown))

  const currentCard = () => cards()[idx()]

  // the card the note panel tracks: the review card in a review round, the
  // pool card during preview (先看后考 is exactly when source context helps).
  // GATED ON REVEAL (user spec 2026-09-17): the matched note section usually
  // contains the answer, so the panel must stay dark until the back is shown
  // — otherwise the note jumps to the answer spot before 显示背面 (leak).
  // Applies to preview / new / review cards alike.
  const noteCard = () => {
    if (phase() === 'review') return revealed() ? currentCard() : undefined
    return undefined
  }
  // card on screen but face-down → panel shows a "revealed 后加载" placeholder
  const noteBlocked = () =>
    (phase() === 'review' && !!currentCard() && !revealed())

  // 溯源 availability (三栏重构 2026-09-21): resolve the on-screen card's
  // reading source ONCE per note id (cached). NOT reveal-gated — the button
  // state (enabled/greyed) reveals nothing about the answer; the detour
  // itself is only reachable by an explicit click. Result feeds the
  // Flashcard 溯源 button (置灰 for orphans, per user spec — never hidden)
  // and openTrace().
  const screenCard = () => {
    if (phase() === 'review') return currentCard()
    return undefined
  }
  createEffect(() => {
    const c = screenCard()
    const noteId = c?.noteId
    if (!noteId || !readingMode()) {
      setTraceSource(null)
      return
    }
    if (srcCache.has(noteId)) {
      setTraceSource(srcCache.get(noteId) ?? null)
      return
    }
    setTraceSource(null)
    api.readingSource(noteId).then(s => {
      srcCache.set(noteId, s)
      // stale-response guard: the card may have swapped while in flight
      if (screenCard()?.noteId === noteId) setTraceSource(s)
    }).catch(() => {
      // dead backend: leave null (button greyed); no negative caching —
      // a transient failure shouldn't permanently disable 溯源
    })
  })
  const traceAvailable = () => traceSource() !== null

  // safety net: the detour only makes sense while its host card is on
  // screen. If the round ends or the section changes underneath (resync,
  // another device answered the card), close it instead of stranding the
  // user on a chunk they can't get back from.
  createEffect(() => {
    if (!traceOpen()) return
    if (section() !== 'study' || !screenCard()) closeTrace()
  })

  // Ring progress for the nav-rail footer (user spec 2026-09-20): the ring
  // moved OUT of the strip above the columns into the rail's bottom-left
  // corner. Derived from the ACTIVE round phase — null when no round is on
  // screen (start/done screens have nothing to count).
  const railRing = () => {
    // works regardless of the visible section: mid-round browsing of
    // 文件/阅读清单 still tracks the active round
    if (phase() === 'review' && currentCard()) return { done: totalDone(), total: roundTotal() }
    if (phase() === 'reading' && rdCurrent()) return { done: rdDone(), total: rdTotal() }
    return null
  }

  // 三栏 gate helpers (user spec 2026-09-21): the side columns only render
  // while a card/chunk is actually on screen — start/done/empty screens stay
  // single-column (same rule the two-column layout has always used).
  const centerOccupied = () =>
    (phase() === 'reading' && !!rdCurrent()) ||
    (phase() === 'review' && !!currentCard())
  // left column feeds on a CHUNK when a chunk is the thing on screen
  // (reading round, or the 溯源 detour); otherwise it resolves by note id
  const leftChunk = () => (traceOpen() ? traceChunk() : (phase() === 'reading' ? rdCurrent() : null))

  return (
    <div class="app-shell">
      <NavRail
        section={section()}
        onNavigate={navigate}
        readingListSize={readingMode() ? readingListSize() : 0}
        ring={railRing()}
      />

      <div class="app">
        {/* ---- 文件 section: read-only corpus browser (tree + viewer) ---- */}
        <Show when={section() === 'files'}>
          <div class="files-page">
            <FilesScreen listedPaths={listedPaths} />
          </div>
        </Show>

        {/* ---- 阅读清单 section: the list manager (was a phase) ---- */}
        <Show when={section() === 'readingList'}>
          <div class="content reading-list-page">
            <ReadingListScreen busy={false} onListChange={rdRefreshCounts} />
          </div>
        </Show>

        {/* ---- 学习 section: the whole funnel ---- */}
        <Show when={section() === 'study'}>
      {/* Round progress used to sit in a full-width strip here (2026-09-17
          alignment fix); the user asked for it in the nav rail's bottom-left
          corner instead (2026-09-20), so the strip is gone — the columns now
          share a top edge on EVERY phase (the strip only rendered mid-round,
          which is why start screens used to misalign). */}

      {/* columns--solo: no card on screen (start/loading/finished) → the side
          columns don't render; center the lone .content column (2026-09-26) */}
      <div
        class="columns"
        classList={{
          'columns--three': isWide3() && centerOccupied(),
          'columns--solo': !centerOccupied(),
        }}
      >
      {/* left column 相关卡片 (三栏重构, user spec 2026-09-21): cards made
          from the SAME note segment (exact provenance — anki-rag similarity
          is retired). Reading round / trace detour → the chunk's own cards
          (+ pre-split ancestors); review/preview → the revealed card's note,
          resolved via /api/reading/source (orphan = 无来源 empty state).
          Same reveal gate as the right NotePanel (a face-down card must not
          leak which segment — and therefore which answer — is on screen). */}
      <Show when={isWide3() && centerOccupied()}>
        <div class="related-column">
          <Show
            when={leftChunk()}
            fallback={
              <RelatedPanel
                noteId={noteCard()?.noteId ?? null}
                cardKey={noteCard()?.cardId ?? null}
                blocked={noteBlocked()}
                refreshToken={relatedToken()}
              />
            }
          >
            <RelatedPanel chunk={leftChunk()!} refreshToken={relatedToken()} />
          </Show>
        </div>
      </Show>
      <div class="content">
        <Show when={phase() === 'loading'}>
          <Loading
            text={loadText()}
            error={loadError()}
            onRetry={() => { setLoadError(false); resync() }}
          />
        </Show>

        <Show when={phase() === 'reading' && rdCurrent() && !traceOpen()}>
          <ReadingCard
            chunk={rdCurrent()!}
            busy={rdBusy()}
            onComplete={() => rdAct('complete')}
            onNext={() => rdAct('next')}
            onSkip={() => rdAct('skip')}
            onAdd={openReadingAdd}
            onCloze={openReadingCloze}
            onSplit={(sel) => doSplit(rdCurrent()!, sel)}
            onEdit={() => openSegEdit(rdCurrent()!)}
            gapPolicy={gapPolicy()}
            onGapPolicyChange={chooseGapPolicy}
            onExit={rdFinish}
          />
        </Show>

        {/* 溯源 detour (2026-09-21): a review/preview card's source segment
            as a temporary reading session. Replaces the suspended card in
            the center column; the round itself is untouched underneath. */}
        <Show when={traceOpen() && traceChunk()}>
          <ReadingCard
            chunk={traceChunk()!}
            variant="trace"
            busy={rdBusy()}
            onComplete={() => {}}
            onNext={() => {}}
            onSkip={() => {}}
            onAdd={openReadingAdd}
            onCloze={openReadingCloze}
            onSplit={(sel) => doSplit(traceChunk()!, sel)}
            onEdit={() => openSegEdit(traceChunk()!)}
            gapPolicy={gapPolicy()}
            onGapPolicyChange={chooseGapPolicy}
            onExit={closeTrace}
            onBack={closeTrace}
          />
        </Show>

        <Show when={phase() === 'start'}>
          <StartScreen
            due={due()}
            newTotal={newTotal()}
            busy={false}
            onBegin={beginFlow}
            previewPool={previewMode() ? previewPool() : null}
            pendingRelease={previewMode() ? pendingRelease() : null}
            previewMode={previewMode()}
            readingAvailable={readingMode() ? readingAvailable() : 0}
            studyModes={studyModes()}
            mode={selectedMode()}
          />
        </Show>

        <Show when={phase() === 'review' && currentCard() && !traceOpen()}>
          <Flashcard
            card={currentCard()!}
            revealed={revealed()}
            traceAvailable={traceAvailable()}
            onTrace={openTrace}
            onEdit={() => setEditOpen(true)}
            onUndo={undo}
            undoEnabled={canUndo()}
            undoBusy={undoBusy()}
            onAdd={openAdd}
            onDelete={() => askDelete(currentCard()!)}
          />
          <ActionArea
            revealed={revealed()}
            answering={answering()}
            onReveal={() => setRevealedFor(currentCard()?.cardId ?? null)}
            onAnswer={answer}
          />
        </Show>
      </div>

      {/* right-hand note panel (知识成体系 Phase 1): wide screens only, and
          only while a card is actually on screen (review round).
          During a reading round — or a 溯源 detour (2026-09-21) — the
          column switches to ReadingPanel: the whole source file anchored at
          the current chunk (user spec 2026-09-19 — 中栏只展示 chunk，右边
          回溯整个笔记看上下文). */}
      {/* Gated on centerOccupied() like the left column (user request
          2026-09-22): start/done/empty screens stay single-column — the
          idle 笔记来源 placeholder box used to render here and clutter the
          开始学习 page. */}
      <Show when={isWide() && centerOccupied()}>
        <div class="note-column">
          <Show
            when={leftChunk()}
            fallback={
              <NotePanel
                noteId={noteCard()?.noteId}
                cardKey={noteCard()?.cardId ?? null}
                blocked={noteBlocked()}
              />
            }
          >
            <ReadingPanel chunk={leftChunk()!} reloadToken={noteReloadToken()} />
          </Show>
        </div>
      </Show>
      </div>{/* /columns */}
        </Show>{/* /study section */}

      <Show when={editOpen() && currentCard()}>
        <EditDialog
          cardId={currentCard()!.cardId}
          onClose={() => setEditOpen(false)}
          onSaved={(q, a) => {
            setCards(prev =>
              prev.map(c =>
                c.cardId === currentCard()!.cardId ? { ...c, question: q, answer: a } : c
              )
            )
            setRevealedFor(null)
          }}
        />
      </Show>

      <Show when={addOpen()}>
        <EditDialog
          mode="add"
          readingSource={addSource()}
          onClose={() => { setAddOpen(false); setAddSource(null) }}
          onAdded={(nid) => onCardAdded(nid)}
        />
      </Show>

      <Show when={clozeOpen()}>
        <ClozeDialog
          initialText={clozeInitial()}
          readingSource={addSource()}
          onClose={() => { setClozeOpen(false); setAddSource(null) }}
          onAdded={(nid) => onCardAdded(nid)}
        />
      </Show>

      {/* 片段编辑器 (user spec 2026-09-23): 宽弹窗 = CM6 源码编辑 + 实时
          预览，两栏各等于三栏布局的一栏宽。unmount on close = fresh CM6
          instance per open (same idiom as EditDialog). */}
      <Show when={segEditOpen() && segEditChunk()}>
        <SegEditDialog
          chunk={segEditChunk()!}
          onClose={() => setSegEditChunk(null)}
          onSaved={onSegEdited}
        />
      </Show>

      <ConfirmDialog
        open={deleteTarget() !== null}
        headline="删除这张卡片？"
        body={deleteTarget() ? `“${deleteTarget()!.preview}” 及其所有卡片都会被永久删除，无法撤销。` : ''}
        confirmLabel="删除"
        busy={deleteBusy()}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      <Snackbar open={snackText() !== null} label={snackText() ?? ''} />
      </div>{/* /.app */}
    </div>
  )
}
