import { Component } from 'solid-js'

interface Props {
  detail: string
  onRefresh: () => void
}

export const EmptyScreen: Component<Props> = (props) => {
  return (
    <div class="screen">
      <md-elevated-card class="screen-card">
        <div class="screen-content">
          <h1 class="screen-title md-typescale-headline-small">没有卡片了</h1>
          <p class="screen-detail md-typescale-body-medium">{props.detail}</p>
          <md-outlined-button onClick={() => props.onRefresh()}>刷新</md-outlined-button>
        </div>
      </md-elevated-card>
    </div>
  )
}
