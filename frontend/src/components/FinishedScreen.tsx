import { Component } from 'solid-js'

interface Props {
  count: number
}

export const FinishedScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">学习结束</h1>
          <p class="screen-detail md-typescale-body-medium">
            本轮共完成 {props.count} 张卡片
          </p>
          <md-filled-button onClick={() => window.location.reload()}>返回</md-filled-button>
        </div>
      </md-elevated-card>
    </div>
  )
}
