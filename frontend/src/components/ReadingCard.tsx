import { Component, Show } from 'solid-js'
import { renderMarkdown } from '../lib/markdown'
import type { ReadingChunk, ReadingChunkStatus } from '../types'
import { IconAdd, IconSkipNext } from './icons'

// Reading card (渐进制卡, user spec 2026-09-19): one note chunk to read and
// turn into cards BY HAND. No reveal gate (this is reading, not a retrieval
// attempt — unlike the preview's 先想后看). The whole source file renders in
// the right column (ReadingPanel) anchored at this chunk's line.
//
// Action rows by status:
//   todo:   开始制卡 (filled, mark_active — STAYS on this chunk) |
//           添加卡片 (outlined) | 无需制卡，跳过 (text)
//   active: 制卡完成 (filled, done+advance) | 添加卡片 (outlined) |
//           下一张 (text, advance without status change — comes back next
//           round) | 跳过 (text)
// Keyboard (App-level, window listener): Space = primary action,
// A = 添加卡片, S = 跳过, N = 下一张.
//
// MD3: same elevated-card lineage as Flashcard/PreviewCard; status shown as
// an assist chip (正在制卡 uses secondary-container tint via CSS class).

const STATUS_LABEL: Record<ReadingChunkStatus, string> = {
  todo: '未读',
  active: '正在制卡',
  done: '制卡完成',
  skipped: '已跳过',
}

interface Props {
  chunk: ReadingChunk
  busy: boolean
  /** mark todo → active (stays on the chunk) */
  onMarkActive: () => void
  /** mark done + advance */
  onComplete: () => void
  /** advance WITHOUT status change (下一张, 稍后继续) */
  onNext: () => void
  /** mark skipped + advance */
  onSkip: () => void
  /** open the add-card dialog pre-linked to this chunk */
  onAdd: () => void
  /** mid-round exit: untouched chunks stay todo/active */
  onExit: () => void
}

export const ReadingCard: Component<Props> = (props) => {
  const crumb = () => {
    const file = props.chunk.path.replace(/^\d{4}\//, '').replace(/\.md$/, '')
    const heads = props.chunk.heading_path.join(' › ')
    return heads ? `${file} › ${heads}` : file
  }
  const isActive = () => props.chunk.status === 'active'
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
              <md-assist-chip
                label={STATUS_LABEL[props.chunk.status]}
                class={`reading-status-chip reading-status-${props.chunk.status}`}
                disabled
              />
              <md-assist-chip label={`本文件进度 ${fileProgress()}`} disabled />
            </md-chip-set>
          </div>

          <div class="reading-crumb md-typescale-label-small">{crumb()}</div>

          <div
            class="card-content reading-chunk-body note-body md-typescale-body-medium"
            innerHTML={renderMarkdown(props.chunk.text)}
          />

          <Show when={props.chunk.cards_created.length > 0}>
            <div class="reading-cards-made md-typescale-label-small">
              已从本片段制卡 {props.chunk.cards_created.length} 张
            </div>
          </Show>
        </div>
      </md-elevated-card>

      <div class="action-area">
        <Show when={!isActive()}>
          <div class="ease-buttons">
            <md-text-button onClick={() => props.onExit()} disabled={props.busy}>
              结束阅读
            </md-text-button>
            <md-text-button onClick={() => props.onSkip()} disabled={props.busy}>
              无需制卡，跳过
            </md-text-button>
            <md-outlined-button onClick={() => props.onAdd()} disabled={props.busy}>
              <md-icon><IconAdd /></md-icon>
              添加卡片
            </md-outlined-button>
            <md-filled-button onClick={() => props.onMarkActive()} disabled={props.busy}>
              开始制卡
            </md-filled-button>
          </div>
        </Show>
        <Show when={isActive()}>
          <div class="ease-buttons">
            <md-text-button onClick={() => props.onExit()} disabled={props.busy}>
              结束阅读
            </md-text-button>
            <md-text-button onClick={() => props.onSkip()} disabled={props.busy}>
              跳过
            </md-text-button>
            <md-text-button onClick={() => props.onNext()} disabled={props.busy}>
              <md-icon><IconSkipNext /></md-icon>
              下一张（稍后继续）
            </md-text-button>
            <md-outlined-button onClick={() => props.onAdd()} disabled={props.busy}>
              <md-icon><IconAdd /></md-icon>
              添加卡片
            </md-outlined-button>
            <md-filled-button onClick={() => props.onComplete()} disabled={props.busy}>
              制卡完成
            </md-filled-button>
          </div>
        </Show>
      </div>
    </div>
  )
}
