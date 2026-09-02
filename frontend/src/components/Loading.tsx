import { Component } from 'solid-js'

interface Props {
  text: string
}

export const Loading: Component<Props> = (props) => {
  return (
    <div class="screen">
      <div class="loading">
        <md-circular-progress indeterminate />
        <div class="loading-text md-typescale-body-medium">{props.text}</div>
      </div>
    </div>
  )
}
