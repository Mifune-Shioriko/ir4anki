import {Component, JSX, onCleanup} from 'solid-js'
import {applyDialogGuard} from '../lib/dialog-guard'

/** Mounted only while open. Material's native modal owns viewport/focus;
 * every key stays here rather than reaching the window study shortcuts. */
export const SplitDialog: Component<{
  title: string
  busy: boolean
  disabled?: boolean
  confirmLabel: string
  onCancel: () => void
  onConfirm: () => void
  children: JSX.Element
}> = props => {
  const trigger = document.activeElement as HTMLElement | null
  onCleanup(() => queueMicrotask(() => { if (trigger?.isConnected) trigger.focus({preventScroll:true}) }))
  const cancel = () => { if (!props.busy) props.onCancel() }
  return <md-dialog class="split-dialog" data-split-dialog open
    aria-label={props.title}
    ref={(el:HTMLElement) => {
      applyDialogGuard(el, cancel)
      el.addEventListener('keydown', event => event.stopPropagation())
    }}
    onClose={cancel}>
    <div slot="headline">{props.title}</div>
    <div slot="content" class="split-dialog-content">{props.children}</div>
    <div slot="actions">
      <md-text-button disabled={props.busy} onClick={cancel}>取消</md-text-button>
      <md-filled-tonal-button disabled={props.busy || props.disabled} onClick={(event:MouseEvent) => { if (!(event?.detail > 1)) props.onConfirm() }}>
        {props.busy ? '正在分割…' : props.confirmLabel}
      </md-filled-tonal-button>
    </div>
  </md-dialog>
}
