import { Component, Show, createEffect, createSignal, on } from 'solid-js'
import { renderMarkdown } from '../lib/markdown'
import { trackChunkSelection } from '../lib/chunk-selection'
import {
  annotateLines,
  annotatedItems,
  previewHeadTail,
  type SplitSelection,
} from '../lib/split-selection'
import type { ReadingChunk, ReadingChunkStatus } from '../types'
import { IconAdd, IconArrowBack, IconEdit, IconPassword } from './icons'

// Reading card (渐进制卡, user spec 2026-09-19; action redesign round 2;
// 三栏重构 2026-09-21). One note chunk to read and turn into cards BY HAND.
// No reveal gate (this is reading, not a retrieval attempt). The whole
// source file renders in the right column (ReadingPanel) anchored at this
// chunk's line; the segment's 已制卡片 list moved to the LEFT column
// (RelatedPanel) — the card body stays clean.
//
// Layout mirrors Flashcard/PreviewCard:
//   header  = status chips (left) + icon actions (right):
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
// far end), the lines highlight and an inline confirm bar appears above
// the body with the exact cut range + a HEAD and TAIL line preview (the
// old head-only snippet couldn't confirm where the cut ended — user spec).
// 确认分割 → POST /api/reading/split with the whole-line range. No
// DOM-selection guessing: the lines ARE the backend's line model, so what
// you click is what gets cut. Gap disposition still follows the 切割语义
// chips: 书签模式 (bookmark, DEFAULT) = the cut lines become a smaller todo
// reading card, the UNREAD tail stays todo (queued), the read prefix sinks
// to background; 提炼模式 (extract) = only the cut lines survive, all gaps
// sink to background.
//
// 添加挖空 still captures the CURRENT text selection inside the chunk body
// (tracked via selectionchange — clicking a toolbar button clears the live
// selection before onClick fires; tracking also survives keyboard
// activation of the icon button).
//
// Two variants:
//   round (default) — dealt inside a reading round: progress chip + bottom
//                     action row + A/C/S/N shortcuts (App-level).
//   trace (溯源)    — a review/preview card's source segment, opened via
//                     溯源: no round actions (there is no reading round),
//                     a 回到复习 icon returns to the suspended card.
//
// Keyboard (App-level, window listener): Space = 制卡完成,
// A = 添加卡片, C = 添加挖空, S = 跳过, N = 下一张.

const STATUS_LABEL: Record<ReadingChunkStatus, string> = {
  todo: '未读',
  active: '正在制卡',
  done: '制卡完成',
  skipped: '已跳过',
  background: '背景',
  container: '已分割',
}

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
  onSplit: (sel: SplitSelection) => void
  /** 编辑片段 (user spec 2026-09-23): open the SegEditDialog for this chunk */
  onEdit?: () => void
  /** 切割语义 (gap_policy, 2026-09-22): bookmark = 进度声明（未读尾巴留在
   *  队列, 默认）, extract = 提炼宣言（未选中部分全部沉背景） */
  gapPolicy: 'bookmark' | 'extract'
  onGapPolicyChange: (p: 'bookmark' | 'extract') => void
  /** mid-round exit: untouched chunks stay todo/active (round only) */
  onExit: () => void
  /** 回到复习 (trace only) */
  onBack?: () => void
}

export const ReadingCard: Component<Props> = (props) => {
  let bodyRef: HTMLDivElement | undefined
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
  // valid 1-line (or 1-block) range — the confirm bar appears immediately.
  // 取消 clears the pick.
  const [pick, setPick] = createSignal<SplitSelection | null>(null)

  const clearPick = () => setPick(null)

  const pickHandle = (start: number, end: number) => {
    if (props.busy) return
    const p = pick()
    if (!p) {
      setPick({ start_line: start, end_line: end })
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
    if (h) e.preventDefault()
  }

  const onBodyClick = (e: MouseEvent) => {
    const t = e.target as HTMLElement | null
    if (!t?.closest) return
    if (t.closest('a')) return // let links navigate, never pick
    const isHandle = !!t.closest('.line-handle')
    const item = t.closest('.line-handle, .line, .line-anchor') as
      | HTMLElement
      | null
    if (!item) return
    if (!isHandle) {
      // Drag guard for line-BODY clicks: a real text selection (or a moved
      // mouse between down and up) is a 添加挖空 cloze seed, not a line
      // pick. Handle clicks skip this — their mousedown preventDefaults
      // (selection stays as the cloze seed) and must ALWAYS pick.
      if (
        Math.abs(e.clientX - downX) > 4 ||
        Math.abs(e.clientY - downY) > 4
      ) {
        return
      }
      const selNow = window.getSelection()
      if (selNow && !selNow.isCollapsed) return
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

  const confirmSplit = () => {
    const p = pick()
    if (!p || props.busy) return
    clearPick()
    props.onSplit(p)
  }

  const crumb = () => {
    const file = props.chunk.path.replace(/^\d{4}\//, '').replace(/\.md$/, '')
    const heads = props.chunk.heading_path.join(' › ')
    return heads ? `${file} › ${heads}` : file
  }
  const fileProgress = () => {
    const done = props.chunk.file_done + props.chunk.file_skipped
    return `${done} / ${props.chunk.file_chunks}`
  }

  return (
    <div class="flashcard-wrapper stage-reading">
      <md-elevated-card class="card-container">
        <div class="card-inner">
          <div class="card-header">
            <div class="card-chips" aria-label="片段信息">
              {trace() && (
                <span class="info-chip md-typescale-label-small">溯源 · 来源片段</span>
              )}
              <span
                class={`info-chip reading-status-chip reading-status-${props.chunk.status} md-typescale-label-small`}
              >
                {STATUS_LABEL[props.chunk.status] ?? props.chunk.status}
              </span>
              <Show when={!trace() && props.chunk.file_chunks > 0}>
                <span class="info-chip md-typescale-label-small">本文件进度 {fileProgress()}</span>
              </Show>
            </div>
            <div class="card-header-actions">
              <md-icon-button
                aria-label="添加卡片"
                disabled={props.busy}
                onClick={() => props.onAdd()}
              >
                <md-icon><IconAdd /></md-icon>
              </md-icon-button>
              <md-icon-button
                aria-label="添加挖空"
                disabled={props.busy}
                onClick={() => props.onCloze(sel.selText(), sel.selBlock())}
              >
                <md-icon><IconPassword /></md-icon>
              </md-icon-button>
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
                <div class="header-actions-sep" aria-hidden="true" />
                <md-icon-button
                  aria-label="回到复习"
                  onClick={() => props.onBack?.()}
                >
                  <md-icon><IconArrowBack /></md-icon>
                </md-icon-button>
              </Show>
            </div>
          </div>

          <div class="reading-crumb md-typescale-label-small">{crumb()}</div>

          {/* 切割语义开关 (gap_policy, 2026-09-22): bookmark = 未读尾巴留在
              队列（默认，user spec「切到哪里=书签」); extract = 未选中部分
              全部沉背景。状态由 App 持有并持久化（localStorage），round 和
              trace 两个变体共用。 */}
          <div class="gap-policy-row">
            <md-chip-set class="gap-policy-chips" aria-label="切割语义">
              <md-filter-chip
                label="书签模式"
                selected={props.gapPolicy === 'bookmark'}
                title="切到哪里=书签：选中部分独立成卡，后面未读的部分留在队列照常推进（已制卡的片段自己当天不重推，次日卡片放行后恢复）"
                onClick={() => props.onGapPolicyChange('bookmark')}
              />
              <md-filter-chip
                label="提炼模式"
                selected={props.gapPolicy === 'extract'}
                title="提炼宣言：只有选中的部分保留，未选中部分全部沉入背景（可在阅读清单提升）"
                onClick={() => props.onGapPolicyChange('extract')}
              />
            </md-chip-set>
            <span class="gap-policy-hint md-typescale-label-small">
              {props.gapPolicy === 'bookmark'
                ? '未读的尾巴留在队列'
                : '未选中部分沉入背景'}
            </span>
          </div>

          {/* 分割确认条 (line-handle picker, 2026-10-01): appears once a
              line handle is clicked; shows the EXACT whole-line cut range
              plus a head AND tail preview of the source lines (user spec:
              the head-only snippet couldn't confirm where the cut ended).
              取消 clears the pick; 确认分割 fires onSplit. */}
          <Show when={pick()}>
            {(p) => {
              const n = () => p().end_line - p().start_line + 1
              const pv = () => previewHeadTail(props.chunk.text, p())
              return (
                <div class="split-confirm md-typescale-label-small" aria-live="polite">
                  <div class="split-confirm__info">
                    <span class="split-confirm__range">
                      将切出第 {p().start_line}–{p().end_line} 行（{n()} 行）
                    </span>
                    <span class="split-confirm__preview" title={pv().headFull}>
                      头：{pv().head || '（空行）'}
                    </span>
                    <Show when={n() > 1}>
                      <span class="split-confirm__preview" title={pv().tailFull}>
                        尾：{pv().tail || '（空行）'}
                      </span>
                    </Show>
                  </div>
                  <div class="split-confirm__actions">
                    <md-text-button
                      class="split-confirm__cancel"
                      disabled={props.busy}
                      onClick={clearPick}
                    >
                      取消
                    </md-text-button>
                    <md-filled-tonal-button
                      class="split-confirm__ok"
                      disabled={props.busy}
                      onClick={confirmSplit}
                    >
                      确认分割
                    </md-filled-tonal-button>
                  </div>
                </div>
              )
            }}
          </Show>

          <div
            class="card-content reading-chunk-body note-body md-typescale-body-medium"
            ref={bodyRef}
            innerHTML={renderMarkdown(props.chunk.text)}
            onMouseDown={onBodyMouseDown}
            onClick={onBodyClick}
          />
        </div>
      </md-elevated-card>

      <Show when={!trace()}>
        <div class="action-area">
          <div class="ease-buttons">
            <md-text-button onClick={() => props.onExit()} disabled={props.busy}>
              结束阅读
            </md-text-button>
            <md-text-button onClick={() => props.onSkip()} disabled={props.busy}>
              {props.chunk.status === 'active' ? '跳过' : '无需制卡，跳过'}
            </md-text-button>
            <md-text-button onClick={() => props.onNext()} disabled={props.busy}>
              下一张（稍后继续）
            </md-text-button>
            <md-filled-button onClick={() => props.onComplete()} disabled={props.busy}>
              制卡完成
            </md-filled-button>
          </div>
        </div>
      </Show>
    </div>
  )
}
