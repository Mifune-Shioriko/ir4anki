import { Component } from 'solid-js'

// Reveal / ease buttons. Good = filled (primary CTA, wider); Again/Hard/Easy
// = outlined, per the agreed design.
interface Props {
  revealed: boolean
  answering: boolean
  onReveal: () => void
  onAnswer: (ease: number) => void
}

export const ActionArea: Component<Props> = (props) => {
  return (
    <div class="action-area">
      {!props.revealed ? (
        <md-filled-tonal-button onClick={() => props.onReveal()} disabled={props.answering}>
          显示答案
        </md-filled-tonal-button>
      ) : (
        <div class="ease-buttons">
          <md-outlined-button class="ease-again" onClick={() => props.onAnswer(1)} disabled={props.answering}>
            Again
          </md-outlined-button>
          <md-outlined-button onClick={() => props.onAnswer(2)} disabled={props.answering}>
            Hard
          </md-outlined-button>
          <md-filled-button class="ease-good" onClick={() => props.onAnswer(3)} disabled={props.answering}>
            Good
          </md-filled-button>
          <md-outlined-button onClick={() => props.onAnswer(4)} disabled={props.answering}>
            Easy
          </md-outlined-button>
        </div>
      )}
    </div>
  )
}
