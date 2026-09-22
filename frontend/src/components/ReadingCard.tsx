import { Component, Show } from 'solid-js'
import { renderMarkdown } from '../lib/markdown'
import { trackChunkSelection } from '../lib/chunk-selection'
import type { ReadingChunk, ReadingChunkStatus } from '../types'
import { IconAdd, IconArrowBack, IconContentCut, IconPassword } from './icons'

// Reading card (渐进制卡, user spec 2026-09-19; action redesign round 2;
// 三栏重构 2026-09-21). One note chunk to read and turn into cards BY HAND.
// No reveal gate (this is reading, not a retrieval attempt). The whole
// source file renders in the right column (ReadingPanel) anchored at this
// chunk's line; the segment's 已制卡片 list moved to the LEFT column
// (RelatedPanel) — the card body stays clean.
//
// Layout mirrors Flashcard/PreviewCard:
//   header  = status chips (left) + icon actions (right):
//             添加卡片 / 添加挖空 / 分割文段 (+ 回到复习 in trace mode)
//   bottom  = status buttons ONLY: 结束阅读 | 无需制卡，跳过 |
//             下一张（稍后继续） | 制卡完成 (filled)
// The old 开始制卡 gate is GONE — chunks auto-mark 正在制卡 when a card or
// cloze is created from them (backend _reading_record_card).
//
// 分割文段 (round-4 split, 2026-09-21; gap_policy 2026-09-22): select text
// in the chunk body → the icon maps the selection to source lines
// (block-granular, see lib/split-selection.ts) → POST /api/reading/split.
// Gap disposition follows the 切割语义 chips: 书签模式 (bookmark, DEFAULT) =
// the selection becomes a smaller todo reading card, the UNREAD tail stays
// todo (queued — the cut is a bookmark, gate paces it behind the selection's
// cards), the read prefix sinks to background; 提炼模式 (extract) = only the
// selection survives, all gaps sink to background (promotable later). The
// parent always becomes a container keeping all provenance. Disabled without
// a selection inside the body.
//
// 添加挖空 captures the CURRENT text selection inside the chunk body
// (tracked via selectionchange — clicking the toolbar button clears the
// live selection before onClick fires; tracking also survives keyboard
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
  /** 分割文段: selection mapped to source-line range RELATIVE to the chunk
   *  text (caller adds line_start-1 for file lines) */
  onSplit: (sel: { start_line: number; end_line: number }) => void
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
    <div class="flashcard-wrapper">
      <md-elevated-card class="card-container">
        <div class="card-inner">
          <div class="card-header">
            <md-chip-set class="card-chips" aria-label="片段信息">
              {trace() && (
                <md-assist-chip label="溯源 · 来源片段" disabled />
              )}
              <md-assist-chip
                label={STATUS_LABEL[props.chunk.status] ?? props.chunk.status}
                class={`reading-status-chip reading-status-${props.chunk.status}`}
                disabled
              />
              <Show when={!trace() && props.chunk.file_chunks > 0}>
                <md-assist-chip label={`本文件进度 ${fileProgress()}`} disabled />
              </Show>
            </md-chip-set>
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
              <md-icon-button
                aria-label="分割文段（先选中正文）"
                title="先在正文选中要独立成卡的段落，再点击分割"
                disabled={props.busy || sel.selLines() === null}
                onClick={() => {
                  const s = sel.selLines()
                  if (s) props.onSplit(s)
                }}
              >
                <md-icon><IconContentCut /></md-icon>
              </md-icon-button>
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
              队列（默认，user spec「切到哪里=书签」）; extract = 未选中部分
              全部沉背景。状态由 App 持有并持久化（localStorage），round 和
              trace 两个变体共用。 */}
          <div class="gap-policy-row">
            <md-chip-set class="gap-policy-chips" aria-label="切割语义">
              <md-filter-chip
                label="书签模式"
                selected={props.gapPolicy === 'bookmark'}
                title="切到哪里=书签：选中部分独立成卡，后面未读的部分留在队列，消化完卡片后自动推回来"
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

          <div
            class="card-content reading-chunk-body note-body md-typescale-body-medium"
            ref={bodyRef}
            innerHTML={renderMarkdown(props.chunk.text)}
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
