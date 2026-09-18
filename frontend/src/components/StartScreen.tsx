import { Component, Show } from 'solid-js'
import { ModeSelector } from './ModeSelector'
import type { StudyModes } from '../types'
import { IconMenuBook } from './icons'

interface Props {
  due: number | null
  newPerRound: number | null
  newTotal: number | null
  busy: boolean
  onBegin: () => void
  /** preview pool size; button hidden when null or 0 */
  previewPool?: number | null
  onToPreview?: () => void
  /** 渐进制卡 (2026-09-19): entry to the reading-list manager; hidden when
   * reading mode is off. Shown even with 0 dealable files so the user can
   * always add/reorder notes. */
  readingMode?: boolean
  readingListSize?: number
  onToReadingList?: () => void
  /** two-tier pacing (2026-09-14): table from the wire + current selection */
  studyModes?: StudyModes | null
  mode?: string
  onModeChange?: (mode: string) => void
}

export const StartScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">开始学习</h1>

          <Show when={props.studyModes}>
            <div class="screen-modes">
              <div class="modes-label md-typescale-label-medium">
                现在有多少时间？
              </div>
              <ModeSelector
                modes={props.studyModes!}
                selected={props.mode ?? 'quick'}
                onSelect={m => props.onModeChange?.(m)}
                disabled={props.busy}
              />
            </div>
          </Show>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>全局待复习</span>
              <span class="stat-value">{props.due ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>牌组新卡池</span>
              <span class="stat-value">{props.newTotal ?? '—'} 张</span>
            </div>
          </div>

          <md-filled-button onClick={() => props.onBegin()} disabled={props.busy}>
            开始
          </md-filled-button>
          {props.previewPool != null && props.previewPool > 0 && (
            <md-text-button onClick={() => props.onToPreview?.()} disabled={props.busy}>
              去预览新卡（{props.previewPool} 张）
            </md-text-button>
          )}
          <Show when={props.readingMode}>
            <md-text-button onClick={() => props.onToReadingList?.()} disabled={props.busy}>
              <md-icon><IconMenuBook /></md-icon>
              阅读清单{props.readingListSize ? `（${props.readingListSize}）` : ''}
            </md-text-button>
          </Show>
        </div>
      </md-elevated-card>
    </div>
  )
}
