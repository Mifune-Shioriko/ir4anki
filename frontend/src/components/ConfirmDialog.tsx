import { Component, Show } from 'solid-js'

// Delete confirmation (防呆, user spec 2026-09-04): MD3 basic dialog —
// centered container (the default md-dialog anchoring, NOT the edit sheet's
// bottom-sheet animation), headline + supporting text + two actions.
// Destructive confirm button is styled error-colored via filled-button
// container/label tokens.
//
// Mounted only while open (Solid <Show>, same lifecycle as EditDialog) so a
// closed dialog is fully out of the DOM — keeps keyboard focus and test
// selectors honest.
interface Props {
  open: boolean
  headline: string
  body: string
  confirmLabel: string
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}

export const ConfirmDialog: Component<Props> = (props) => {
  return (
    <Show when={props.open}>
      <md-dialog
        class="confirm-dialog"
        open
        onClose={() => props.onCancel()}
      >
        <div slot="headline">{props.headline}</div>
        <div slot="content" class="confirm-text md-typescale-body-medium">
          {props.body}
        </div>
        <div slot="actions">
          <md-text-button onClick={() => props.onCancel()} disabled={props.busy}>
            取消
          </md-text-button>
          <md-filled-button
            class="danger-button"
            onClick={() => props.onConfirm()}
            disabled={props.busy}
          >
            {props.confirmLabel}
          </md-filled-button>
        </div>
      </md-dialog>
    </Show>
  )
}
