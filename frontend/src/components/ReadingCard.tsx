import {SplitDialog} from './SplitDialog'
import { StudyCardShell, StudyToolbar, StudyActionDock } from './StudyCardShell'
import { Component, Show, createEffect, createMemo, createSignal, on, onCleanup, onMount } from 'solid-js'
import { renderMarkdown } from '../lib/markdown'
import { trackChunkSelection } from '../lib/chunk-selection'
import {
  annotateLines,
  annotatedItems,
  previewHeadTail,
  resolveLineAt,
  type SplitSelection,
} from '../lib/split-selection'
import type { ReadingChunk } from '../types'
import { IconAdd, IconArrowBack, IconClose, IconContentCut, IconEdit, IconPassword, IconUndo } from './icons'

// Reading card (渐进制卡, user spec 2026-09-19; action redesign round 2;
// 三栏重构 2026-09-21). One note chunk to read and turn into cards BY HAND.
// No reveal gate (this is reading, not a retrieval attempt). The whole
// source file renders in the right column (ReadingPanel) anchored at this
// chunk's line; the segment's 已制卡片 list moved to the LEFT column
// (RelatedPanel) — the card body stays clean.
//
// Layout mirrors Flashcard/PreviewCard:
//   header  = trace identification (left) + icon actions (right):
//             添加卡片 / 添加挖空 (+ 编辑片段, + 回到复习 in trace mode)
//   bottom  = status buttons ONLY: 结束阅读 | 无需制卡，跳过 |
//             下一张（稍后继续） | 制卡完成 (filled)
// The old 开始制卡 gate is GONE — chunks auto-mark 正在制卡 when a card or
// cloze is created from them (backend _reading_record_card).
//
// 分割文段 (LINE-HANDLE model, 2026-10-01 — replaces the text-selection
// flow; WHOLE-LINE click target 2026-10-03): every rendered source line
// carries a small dot handle in the left gutter (atomic blocks — code
// fences, tables, $$ formulas, <hr> — get ONE handle for the whole block).
// The dot is the affordance, but clicking ANYWHERE on the rendered line
// picks it (a drag/text-selection is the cloze seed and never picks).
// Click the first line, click the last line (further clicks re-adjust the
// far end), the lines highlight; the permanent toolbar scissors opens a
// viewport modal with the exact cut range + a HEAD and TAIL line preview (the
// old head-only snippet couldn't confirm where the cut ended — user spec).
// 确认分割 → POST /api/reading/split with the whole-line range. No
// DOM-selection guessing: the lines ARE the backend's line model, so what
// you click is what gets cut. Selected lines become todo children; the
// unread tail remains todo for future rounds; prefix and middle gaps become
// background.
//
// 添加挖空 still captures the CURRENT text selection inside the chunk body
// (tracked via selectionchange — clicking a toolbar button clears the live
// selection before onClick fires; tracking also survives keyboard
// activation of the icon button).
//
// Two variants:
//   round (default) — dealt inside a reading round: bottom
//                     action row + A/C/S/N shortcuts (App-level).
//   trace (溯源)    — a review/preview card's source segment, opened via
//                     溯源: no round actions (there is no reading round),
//                     a 回到复习 icon returns to the suspended card.
//
// Keyboard (App-level, window listener): Space = 制卡完成,
// A = 添加卡片, C = 添加挖空, S = 跳过, N = 下一张.

interface Props {
  chunk: ReadingChunk
  busy: boolean
  /** trace variant (溯源 detour from a review/preview card) */
  variant?: 'round' | 'trace'
  /** mark done + advance (round only) */
  onComplete: () => void
  /** advance WITHOUT status change (下一张, 稍后继续; round only) */
  onNext: () => void
  /** mark skipped + advance (round only) */
  onSkip: () => void
  /** open the add-card dialog pre-linked to this chunk */
  onAdd: () => void
  /** open the cloze dialog; selText = selection inside the chunk body,
   * selBlock = text of the block element containing it ('' = no selection).
   * The block disambiguates WHICH occurrence of a repeated word the user
   * meant when wrapping in {{c1::}}. */
  onCloze: (selText: string, selBlock: string) => void
  /** 分割文段: whole-line range RELATIVE to the chunk text (caller adds
   *  line_start-1 for file lines). Comes from the line-handle picker. */
  onSplit: (sel: SplitSelection) => void | boolean | Promise<void | boolean>
  /** 编辑片段 (user spec 2026-09-23): open the SegEditDialog for this chunk */
  onEdit?: () => void
  /** mid-round exit: untouched chunks stay todo/active (round only) */
  onExit: () => void
  /** Whole-flow controls supplied only for the READING phase. */
  onUndo?: () => void
  undoEnabled?: boolean
  undoBusy?: boolean
  onExitRound?: () => void
  exitDisabled?: boolean
  /** 回到复习 (trace only) */
  onBack?: () => void
}

export const ReadingCard: Component<Props> = (props) => {
  // Keep identical source HTML stable when chunk metadata changes. Rewriting
  // innerHTML would discard the line handles already annotated for this text.
  const bodyHtml = createMemo(() => renderMarkdown(props.chunk.text))
  let bodyRef: HTMLDivElement | undefined
  // Let Material activate the control without the page's Space-to-complete shortcut.
  const roundControlKeyDown = (event: KeyboardEvent) => {
    if (event.key === ' ' || event.key === 'Enter') event.stopPropagation()
  }
  const trace = () => (props.variant ?? 'round') === 'trace'

  const sel = trackChunkSelection(
    () => bodyRef,
    () => [props.chunk.chunk_key, props.chunk.path] as const,
  )

  // ---- line-handle split picker (2026-10-01) ----
  // First click pins one endpoint; every later click moves the NEAREST
  // endpoint of the pending range (so 4→5→3 yields 3–5, not 3–4 — a
  // mis-click or a late "also need the line above" just needs one more
  // click without losing the other end). A single click already forms a
  // valid 1-line (or 1-block) range — the toolbar scissors becomes available.
  // Closing confirmation preserves the pick for adjustment or retry.
  const [pick, setPick] = createSignal<SplitSelection | null>(null)

  const [splitOpen, setSplitOpen] = createSignal(false)
  const [splitPending, setSplitPending] = createSignal(false)
  const [splitError, setSplitError] = createSignal('')
  const splitAvailable = () => props.chunk.seg_id != null && props.chunk.status !== 'container'
  const splitDisabled = () => !pick() || props.busy || splitPending() || !splitAvailable()
  const splitName = () => !splitAvailable() ? '当前片段不可分割' : !pick() ? '请先点选正文行或圆点，选择分割范围' : '分割选中行'
  let disposed = false
  let splitEpoch = 0
  onCleanup(() => { disposed = true; ++splitEpoch })
  const clearPick = () => { ++splitEpoch; setPick(null); setSplitOpen(false); setSplitPending(false); setSplitError('') }

  const onDocKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && pick() !== null && !splitOpen() && !props.busy) {
      if (document.querySelector?.('md-dialog[open], .card-drawer-layer')) return
      clearPick()
    }
  }
  onMount(() => document.addEventListener('keydown', onDocKeyDown))
  onCleanup(() => document.removeEventListener('keydown', onDocKeyDown))

  const pickHandle = (start: number, end: number) => {
    if (props.busy || splitOpen() || !splitAvailable()) return
    const p = pick()
    if (!p) {
      setPick({ start_line: start, end_line: end })
      return
    }
    // Clicking any already-selected line/dot cancels the current selection
    // so the user can immediately deselect or start a fresh selection.
    if (start <= p.end_line && end >= p.start_line) {
      clearPick()
      return
    }
    const mid = start + (end - start) / 2
    const dStart = Math.abs(mid - p.start_line)
    const dEnd = Math.abs(mid - p.end_line)
    if (dStart <= dEnd) {
      // move the start endpoint; keep order normalized
      setPick({
        start_line: Math.min(start, p.end_line),
        end_line: Math.max(end, p.end_line),
      })
    } else {
      setPick({
        start_line: Math.min(start, p.start_line),
        end_line: Math.max(end, p.start_line),
      })
    }
  }

  // Click target = the WHOLE rendered line (span.line / div.line-anchor),
  // not just the gutter dot (user spec 2026-10-03: 点选对应行就选中切片).
  // The dot stays as the visual affordance; hovering anywhere on the line
  // lights it up (CSS). A DRAG (text selection) must NOT pick a line — it
  // is the 添加挖空 cloze seed — so mousedown coords are recorded and a
  // moved/non-collapsed click is ignored.
  let downX = 0
  let downY = 0

  const onBodyMouseDown = (e: MouseEvent) => {
    downX = e.clientX
    downY = e.clientY
    const h = (e.target as HTMLElement | null)?.closest?.('.line-handle')
    // preventDefault keeps the click from focusing the button (Space must
    // stay 制卡完成) and from starting a text selection (the cloze seed).
    // Also prevent quick second-click (double-click) word highlight when a
    // line selection is already active so clicking again cleanly deselects.
    if (h || (e.detail > 1 && pick() !== null)) e.preventDefault()
  }

  const onBodyClick = (e: MouseEvent) => {
    const t = e.target as HTMLElement | null
    if (!t?.closest) return
    if (t.closest('a')) return // let links navigate, never pick
    const isHandle = !!t.closest('.line-handle')
    let item = t.closest('.line-handle, .line, .line-anchor') as
      | HTMLElement
      | null
    if (!item && bodyRef && typeof document.elementFromPoint === 'function') {
      item = resolveLineAt(bodyRef, e.clientX, e.clientY)?.el ?? null
    }
    if (!item) {
      const selNow =
        typeof window !== 'undefined' ? window.getSelection?.() : null
      if (!selNow || selNow.isCollapsed) clearPick()
      return
    }
    if (!isHandle) {
      // Drag guard for line-BODY clicks: a real text selection (dragged
      // mouse between down and up) is a 添加挖空 cloze seed, not a line
      // pick. Handle clicks skip this — their mousedown preventDefaults
      // (selection stays as the cloze seed) and must ALWAYS pick.
      const moved =
        Math.abs(e.clientX - downX) > 4 ||
        Math.abs(e.clientY - downY) > 4
      const selNow =
        typeof window !== 'undefined' ? window.getSelection?.() : null
      if (moved && (!e.detail || e.detail === 1) && selNow && !selNow.isCollapsed) {
        return
      }
      if (
        Math.abs(e.clientX - downX) > 24 ||
        Math.abs(e.clientY - downY) > 24
      ) {
        return
      }
    }
    const s = parseInt(item.dataset.lineStart || '0', 10)
    const en = parseInt(item.dataset.lineEnd || item.dataset.lineStart || '0', 10)
    if (s) pickHandle(s, Math.max(en, s))
  }

  const applySelClasses = () => {
    if (!bodyRef) return
    const p = pick()
    for (const { el, start, end } of annotatedItems(bodyRef)) {
      const hit =
        p !== null && start <= p.end_line && end >= p.start_line
      el.classList.toggle('line-sel', hit)
    }
  }

  // ---- hover indicator (user spec 2026-10-05) ----
  // CSS :hover alone flickers in the line-height gaps of a wrapped source
  // line (gap belongs to no box → hover drops). mousemove + resolveLineAt
  // treats each source line as ONE block: anywhere in the band lights the
  // gutter dot (.line-hover), so sweeping the mouse never blinks. The row
  // background 荧光 is gone — the dot is the only indicator.
  let hoverEl: HTMLElement | null = null
  const clearHover = () => {
    if (hoverEl) {
      hoverEl.classList.remove('line-hover')
      hoverEl = null
    }
  }
  const onBodyMove = (e: MouseEvent) => {
    if (!bodyRef) return
    const hit = resolveLineAt(bodyRef, e.clientX, e.clientY)
    const el = hit?.el ?? null
    if (el === hoverEl) return
    clearHover()
    if (el) {
      el.classList.add('line-hover')
      hoverEl = el
    }
  }
  const onBodyLeave = () => clearHover()

  // annotate after every (re)render of the body; the token is the chunk
  // text itself — identical text re-annotating is a no-op, a chunk swap
  // re-runs the walk. queueMicrotask: Solid's innerHTML binding is itself an
  // effect created AFTER this one (same batch, creation order), so the
  // annotation must wait one microtask to see the NEW rendered content.
  createEffect(
    on(
      () => [props.chunk.text, props.chunk.chunk_key, props.chunk.path] as const,
      () => {
        clearPick()
        clearHover()
        const text = props.chunk.text
        queueMicrotask(() => {
          if (!bodyRef) return
          annotateLines(bodyRef, text)
          applySelClasses()
        })
      },
    ),
  )
  createEffect(() => {
    pick()
    applySelClasses()
  })

  const confirmSplit = async () => {
    const p = pick()
    if (!splitOpen() || !p || splitDisabled()) return
    setSplitPending(true)
    setSplitError('')
    const epoch = splitEpoch
    const current = () => !disposed && epoch === splitEpoch
    try {
      const result = await props.onSplit(p)
      if (current() && result === false) setSplitError('分割未完成，请核对错误提示后重试')
    } catch (e) {
      if (current()) setSplitError((e as Error).message || '分割失败，请重试')
    } finally {
      if (current()) setSplitPending(false)
    }
  }

  const crumb = () => {
    const file = props.chunk.path.replace(/^\d{4}\//, '').replace(/\.md$/, '')
    const heads = props.chunk.heading_path.join(' › ')
    return heads ? `${file} › ${heads}` : file
  }

  return (
    <StudyCardShell stage="reading" source={crumb()} toolbar={
      <StudyToolbar label="阅读操作">
              <Show when={!trace() && props.onExitRound}>
                <md-icon-button aria-label="撤销上次评分" title="撤销上次评分"
                  disabled={!props.undoEnabled || props.undoBusy || props.busy}
                  onKeyDown={roundControlKeyDown} onClick={() => props.onUndo?.()}>
                  <md-icon><IconUndo /></md-icon>
                </md-icon-button>
                <md-icon-button aria-label="退出本轮" title="退出本轮"
                  disabled={props.exitDisabled}
                  onKeyDown={roundControlKeyDown} onClick={() => props.onExitRound?.()}>
                  <md-icon><IconClose /></md-icon>
                </md-icon-button>
              </Show>
              <md-icon-button
                aria-label="添加卡片" title="添加卡片"
                disabled={props.busy}
                onClick={() => props.onAdd()}
              >
                <md-icon><IconAdd /></md-icon>
              </md-icon-button>
              <md-icon-button
                aria-label="添加挖空" title="添加挖空"
                disabled={props.busy}
                onClick={() => props.onCloze(sel.selText(), sel.selBlock())}
              >
                <md-icon><IconPassword /></md-icon>
              </md-icon-button>
              <span class="split-action-hint" title={splitName()}>
                <md-icon-button data-split-action aria-label={splitName()} title={splitName()}
                  disabled={splitDisabled()} onClick={() => { if (!splitDisabled()) { setSplitError(''); setSplitOpen(true) } }}>
                  <md-icon><IconContentCut /></md-icon>
                </md-icon-button>
              </span>
              {/* 编辑片段 (user spec 2026-09-23): CM6 弹窗改 .md 原文；
                  container 只读（历史锚，后端也会 409），trace 卡片无 seg 时可编辑 */}
              <Show when={props.onEdit}>
                <md-icon-button
                  aria-label="编辑片段"
                  title="编辑这个片段的 Markdown 原文（保存后写回笔记文件）"
                  disabled={props.busy || props.chunk.status === 'container' || props.chunk.seg_id == null}
                  onClick={() => props.onEdit?.()}
                >
                  <md-icon><IconEdit /></md-icon>
                </md-icon-button>
              </Show>
              <Show when={trace()}>
                <md-icon-button
                  aria-label="回到复习" title="回到复习"
                  onClick={() => props.onBack?.()}
                >
                  <md-icon><IconArrowBack /></md-icon>
                </md-icon-button>
              </Show>
      </StudyToolbar>
    } dock={
      <Show when={!trace()}>
        <StudyActionDock>
          <md-text-button onClick={() => props.onExit()} disabled={props.busy}>结束阅读</md-text-button>
          <md-text-button onClick={() => props.onSkip()} disabled={props.busy}>{props.chunk.status === 'active' ? '跳过' : '无需制卡，跳过'}</md-text-button>
          <md-text-button onClick={() => props.onNext()} disabled={props.busy}>下一张（稍后继续）</md-text-button>
          <md-filled-button onClick={() => props.onComplete()} disabled={props.busy}>制卡完成</md-filled-button>
        </StudyActionDock>
      </Show>
    }>
          <Show when={splitOpen() && pick()}>
            {(selected) => {
              const p = selected
              const pv = () => previewHeadTail(props.chunk.text, p())
              return <SplitDialog title="确认分割文段" busy={props.busy || splitPending()}
                disabled={!splitAvailable()} confirmLabel="确认分割"
                onCancel={() => setSplitOpen(false)} onConfirm={() => void confirmSplit()}>
                <p>将切出第 {p().start_line}–{p().end_line} 行（{p().end_line - p().start_line + 1} 行）</p>
                <div class="split-line-previews">
                  <p title={pv().headFull}>头：{pv().head || '（空行）'}</p>
                  <p title={pv().tailFull}>尾：{pv().tail || '（空行）'}</p>
                </div>
                <Show when={splitError()}><p class="lecture-error" role="alert">{splitError()}</p></Show>
              </SplitDialog>
            }}
          </Show>

          <div
            class="card-content study-content reading-chunk-body"
            ref={bodyRef}
            innerHTML={bodyHtml()}
            onMouseDown={onBodyMouseDown}
            onClick={onBodyClick}
            onMouseMove={onBodyMove}
            onMouseLeave={onBodyLeave}
          />
    </StudyCardShell>
  )
}
