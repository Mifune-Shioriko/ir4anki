import { Component } from 'solid-js'

// Round progress. Per the verified backend contract:
//   value = totalDone / roundTotal  (never totalDone + idx — both move in
//   lockstep after each answer and adding them double-counts).
interface Props {
  done: number
  total: number
  reviewDone: number
  reviewTotal: number
  newDone: number
  newTotal: number
}

export const ProgressBar: Component<Props> = (props) => {
  const fraction = () => (props.total > 0 ? Math.min(props.done / props.total, 1) : 0)
  return (
    <div class="progress-area">
      <div class="progress-row">
        <md-linear-progress class="progress-bar" value={fraction()} />
        <span class="progress-text md-typescale-label-large">
          {props.done}/{props.total}
        </span>
      </div>
      <div class="progress-split md-typescale-label-medium">
        <span>复习 {props.reviewDone}/{props.reviewTotal}</span>
        <span>新卡 {props.newDone}/{props.newTotal}</span>
      </div>
    </div>
  )
}
