import { Component, Show } from 'solid-js'

// Preview round complete. Reports what happened this round (released vs
// deferred) and offers: another preview batch (if any remain available
// today), or moving on to regular review.
interface Props {
  approved: number
  deferred: number
  pool: number | null
  available: number | null
  due: number | null
  busy: boolean
  /** preview batch size from the backend (never hardcode) */
  perRound: number
  onUndo: () => void
  canUndo: boolean
  undoBusy: boolean
  onMore: () => void
  onToReview: () => void
  onFinish: () => void
}

export const PreviewDoneScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">本轮预览完成</h1>
          <p class="screen-detail md-typescale-body-small">
            今天放行的卡明天才会开考——睡一觉再测，才是真提取。
          </p>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>已放行（明天进入学习队列）</span>
              <span class="stat-value">{props.approved} 张</span>
            </div>
            <div class="stat-row">
              <span>明天再看</span>
              <span class="stat-value">{props.deferred} 张</span>
            </div>
            <md-divider />
            <div class="stat-row">
              <span>预览池剩余</span>
              <span class="stat-value">{props.pool ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>全局待复习</span>
              <span class="stat-value">{props.due ?? '—'} 张</span>
            </div>
          </div>

          <div class="screen-actions">
            <md-filled-button onClick={() => props.onToReview()} disabled={props.busy}>
              开始复习
            </md-filled-button>
          </div>
          <div class="screen-actions">
            <Show when={props.canUndo}>
              <md-text-button onClick={() => props.onUndo()} disabled={props.undoBusy}>
                撤销上一步
              </md-text-button>
            </Show>
            <Show when={(props.available ?? 0) > 0}>
              <md-text-button onClick={() => props.onMore()} disabled={props.busy}>
                再预览 {props.perRound} 张
              </md-text-button>
            </Show>
            <md-text-button onClick={() => props.onFinish()} disabled={props.busy}>
              结束
            </md-text-button>
          </div>
        </div>
      </md-elevated-card>
    </div>
  )
}
