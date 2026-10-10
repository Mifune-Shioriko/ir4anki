import { Component } from 'solid-js'

// Reveal / ease buttons. Expressive color language (2026-09-27 桌面重设计):
// Again = filled-tonal on error-container, Hard = outlined (neutral),
// Good = filled primary, taller + wider (the primary CTA), Easy =
// filled-tonal on its default secondary-container. Keycap hints surface the
// existing keyboard shortcuts (Space / 1-4) so they're discoverable.
interface Props {
  nextIntervals?: [string, string, string, string] | null
  revealed: boolean
  answering: boolean
  onReveal: () => void
  onAnswer: (ease: number) => void
}

const Keycap = (k: string) => <span class="keycap" aria-hidden="true">{k}</span>

export const ActionArea: Component<Props> = (props) => {
  return (
    <div class="action-area">
      {!props.revealed ? (
        <md-filled-tonal-button onClick={() => props.onReveal()} disabled={props.answering}>
          显示答案
          {Keycap('Space')}
        </md-filled-tonal-button>
      ) : (
        <div class="ease-buttons">
          <md-filled-tonal-button class="ease-again" onClick={() => props.onAnswer(1)} disabled={props.answering}>
            Again
            {Keycap('1')}
          </md-filled-tonal-button>
          <md-outlined-button onClick={() => props.onAnswer(2)} disabled={props.answering}>
            Hard
            {Keycap('2')}
          </md-outlined-button>
          <md-filled-button class="ease-good" onClick={() => props.onAnswer(3)} disabled={props.answering}>
            Good
            {Keycap('3')}
          </md-filled-button>
          <md-filled-tonal-button class="ease-easy" onClick={() => props.onAnswer(4)} disabled={props.answering}>
            Easy
            {Keycap('4')}
          </md-filled-tonal-button>
        </div>
      )}
    </div>
  )
}
