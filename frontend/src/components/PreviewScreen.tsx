import { Component } from 'solid-js'

// Preview-mode start screen (先看后考). Shown instead of the study start
// screen when preview mode is on: its job is to make the zero-stakes nature
// of preview explicit — reading together, no grading, no wrong answers.
interface Props {
  pool: number | null
  available: number | null
  due: number | null
  busy: boolean
  onStart: () => void
  onSkipToReview: () => void
}

export const PreviewScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">预览新卡</h1>
          <p class="screen-detail md-typescale-body-medium">
            先看后考：正反面一起展示，只读不评分，没有答错这回事。
            看完一张选「放行」，它才会进入正式学习队列。
          </p>

          <div class="screen-stats md-typescale-body-medium">
            <div class="stat-row">
              <span>预览池待预览</span>
              <span class="stat-value">{props.pool ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>今天可预览</span>
              <span class="stat-value">{props.available ?? '—'} 张</span>
            </div>
            <div class="stat-row">
              <span>全局待复习</span>
              <span class="stat-value">{props.due ?? '—'} 张</span>
            </div>
          </div>

          <div class="screen-actions">
            <md-filled-button onClick={() => props.onStart()} disabled={props.busy}>
              开始预览
            </md-filled-button>
          </div>
          <div class="screen-actions">
            <md-text-button onClick={() => props.onSkipToReview()} disabled={props.busy}>
              跳过，直接复习
            </md-text-button>
          </div>
        </div>
      </md-elevated-card>
    </div>
  )
}
