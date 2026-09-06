import { Component, Show } from 'solid-js'

interface Props {
  count: number
  /** preview-only exit (2026-09-06): set when the user finished straight
   * from the preview flow (mid-round or on the preview done screen). The
   * review count would be a misleading 0 then, so show preview stats. */
  preview?: { approved: number; deferred: number; pool: number | null } | null
}

export const FinishedScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">学习结束</h1>

          <Show when={!props.preview}>
            <p class="screen-detail md-typescale-body-medium">
              本轮共完成 {props.count} 张卡片
            </p>
          </Show>

          <Show when={props.preview}>
            <div class="screen-stats md-typescale-body-medium">
              <div class="stat-row">
                <span>本次预览放行</span>
                <span class="stat-value">{props.preview!.approved} 张</span>
              </div>
              <div class="stat-row">
                <span>明天再看</span>
                <span class="stat-value">{props.preview!.deferred} 张</span>
              </div>
              <md-divider />
              <div class="stat-row">
                <span>预览池剩余</span>
                <span class="stat-value">{props.preview!.pool ?? '—'} 张</span>
              </div>
            </div>
            <p class="screen-detail md-typescale-body-medium">
              未处理的预览卡留在池里，随时可以继续。今天放行的 {props.preview!.approved} 张卡明天进入学习队列。
            </p>
          </Show>

          <md-filled-button onClick={() => window.location.reload()}>返回</md-filled-button>
        </div>
      </md-elevated-card>
    </div>
  )
}
