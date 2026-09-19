import { Component } from 'solid-js'

// Round progress as an MD3-style circular indicator (user spec 2026-09-19
// round 3): replaces the old md-linear-progress + done/total text + the
// 复习/新卡 detail row. The ring center carries the SAME "done/total" number
// that used to sit right of the bar — nothing else. One component serves
// all three round kinds (review / preview / reading); App derives the
// numbers from the active phase.
//
// Hand-built SVG (md-circular-progress has no center-label slot):
// rotate(-90°) starts the arc at 12 o'clock; stroke-dashoffset transitions
// for the sweep animation (same idiom as the old stats gauge).

interface Props {
  done: number
  total: number
}

const R = 26
const CIRC = 2 * Math.PI * R

export const ProgressRing: Component<Props> = (props) => {
  const fraction = () =>
    props.total > 0 ? Math.min(props.done / props.total, 1) : 0
  return (
    <div
      class="progress-ring"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={props.total}
      aria-valuenow={props.done}
    >
      <svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true">
        <circle class="progress-ring__track" cx="32" cy="32" r={R} />
        <circle
          class="progress-ring__arc"
          cx="32"
          cy="32"
          r={R}
          stroke-dasharray={`${CIRC}`}
          stroke-dashoffset={`${CIRC * (1 - fraction())}`}
        />
      </svg>
      <span class="progress-ring__text md-typescale-label-large">
        {props.done}/{props.total}
      </span>
    </div>
  )
}
