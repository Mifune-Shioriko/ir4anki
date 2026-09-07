import { Component, Show } from 'solid-js'

interface Props {
  text: string
  /** set when the load FAILED: swap the spinner for an error + retry card
   * (audit item #2 — previously the spinner stayed up forever) */
  error?: boolean
  onRetry?: () => void
}
export const Loading: Component<Props> = (props) => {
  return (
    <div class="screen">
      <Show when={!props.error}>
        <div class="loading">
          <md-circular-progress indeterminate />
          <div class="loading-text md-typescale-body-medium">{props.text}</div>
        </div>
      </Show>
      <Show when={props.error}>
        <md-elevated-card class="screen-card">
          <div class="screen-content">
            <h1 class="screen-title md-typescale-headline-small">加载失败</h1>
            <p class="screen-detail md-typescale-body-medium">{props.text}</p>
            <div class="screen-actions">
              <md-filled-button onClick={() => props.onRetry?.()}>重试</md-filled-button>
              <md-text-button onClick={() => location.reload()}>刷新页面</md-text-button>
            </div>
          </div>
        </md-elevated-card>
      </Show>
    </div>
  )
}
