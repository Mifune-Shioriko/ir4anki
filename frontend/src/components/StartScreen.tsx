import { Component } from 'solid-js'

interface Props {
  due: number | null
  newPerRound: number | null
  newTotal: number | null
  busy: boolean
  onBegin: () => void
  /** preview pool size; button hidden when null or 0 */
  previewPool?: number | null
  onToPreview?: () => void
}

export const StartScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">开始学习</h1>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>全局待复习</span>
              <span class="stat-value">{props.due ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>每轮新卡上限</span>
              <span class="stat-value">{props.newPerRound ?? '—'} 张</span>
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
        </div>
      </md-elevated-card>
    </div>
  )
}
