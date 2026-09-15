import { Component, Show } from 'solid-js'

// MD3 snackbar (spec: m3.material.io/components/snackbar). @material/web has
// NO snackbar web component, so this is plain HTML styled strictly with sys
// tokens — same pattern as TopAppBar. Container = inverse-surface, supporting
// text = inverse-on-surface / body-medium, shape = extra-small (4px), action
// = inverse-primary text button. Fixed bottom-center, single-line label.
interface Props {
  open: boolean
  label: string
  actionLabel?: string
  onAction?: () => void
}

export const Snackbar: Component<Props> = (props) => {
  return (
    <Show when={props.open}>
      <div class="snackbar" role="status" aria-live="polite">
        <span class="snackbar__label md-typescale-body-medium">{props.label}</span>
        <Show when={props.actionLabel && props.onAction}>
          <md-text-button
            class="snackbar__action"
            onClick={() => props.onAction?.()}
          >
            {props.actionLabel}
          </md-text-button>
        </Show>
      </div>
    </Show>
  )
}
