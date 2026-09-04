import { Component } from 'solid-js'
import { Show } from 'solid-js'

interface Props {
  count: number
  due: number | null
  newTotal: number | null
  reviewDone: number
  reviewTotal: number
  newDone: number
  newTotalBatch: number
  finishing: boolean
  canUndo: boolean
  undoBusy: boolean
  onUndo: () => void
  onContinue: () => void
  onFinish: () => void
  /** preview pool size; button hidden when null or 0 */
  previewPool?: number | null
  onToPreview?: () => void
}

export const DoneScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">本轮完成</h1>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>复习卡</span>
              <span class="stat-value">{props.reviewDone} / {props.reviewTotal}</span>
            </div>
            <div class="stat-row">
              <span>新卡</span>
              <span class="stat-value">{props.newDone} / {props.newTotalBatch}</span>
            </div>
            <md-divider />
            <div class="stat-row total">
              <span>合计</span>
              <span class="stat-value">{props.count} 张</span>
            </div>
          </div>

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

          <div class="screen-actions">
            <md-outlined-button onClick={() => props.onContinue()} disabled={props.finishing}>
              继续复习
            </md-outlined-button>
            <md-filled-button onClick={() => props.onFinish()} disabled={props.finishing}>
              结束学习
            </md-filled-button>
          </div>
          {props.previewPool != null && props.previewPool > 0 && (
            <div class="screen-actions">
              <md-text-button onClick={() => props.onToPreview?.()} disabled={props.finishing}>
                去预览新卡（{props.previewPool} 张）
              </md-text-button>
            </div>
          )}

          {/* last-answer undo — the card header button is gone on this screen */}
          <Show when={props.canUndo}>
            <div class="screen-actions">
              <md-text-button onClick={() => props.onUndo()} disabled={props.undoBusy}>
                撤销上一张作答
              </md-text-button>
            </div>
          </Show>
        </div>
      </md-elevated-card>
    </div>
  )
}
