import { Component, Show } from 'solid-js'

// Preview-mode start screen (先想后看). Shown instead of the study start
// screen when preview mode is on: its job is to make the zero-stakes nature
// of preview explicit — try to recall first, then check; no grading, no
// wrong answers. Approved cards are released the NEXT day (2026-09-07).
interface Props {
  pool: number | null
  available: number | null
  due: number | null
  /** approved today, still suspended — released into the queue tomorrow */
  pendingRelease?: number | null
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
            先想后看：只显示问题，自己先试着回忆，再点开答案对照。不评分，没有答错这回事。
            看完选「放行」，它会在<span class="em">明天</span>进入学习队列——睡一觉再考，记得才牢。
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
            <Show when={(props.pendingRelease ?? 0) > 0}>
              <div class="stat-row">
                <span>今天已放行（明天开考）</span>
                <span class="stat-value">{props.pendingRelease} 张</span>
              </div>
            </Show>
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
