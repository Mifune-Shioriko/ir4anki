import { createSignal, createUniqueId, onCleanup, onMount, Show, type JSX } from 'solid-js'
import { Portal } from 'solid-js/web'
import { IconClose } from './icons'
import '../card-drawer.css'

/** Non-modal authoring pane with a visual-only scrim, no focus trap or
 * outside-click dismissal. Never shrink the source to fit the editor. */
export function CardDrawer(props: {
  title: string
  onClose: () => void
  busy?: boolean
  actions?: JSX.Element
  children: JSX.Element
}) {
  const titleId = createUniqueId()
  const previousFocus = document.activeElement
  let panel!: HTMLElement
  const [placement, setPlacement] = createSignal<'side' | 'bottom'>('bottom')
  const [width, setWidth] = createSignal(360)
  const updatePlacement = () => {
    const source = document.querySelector('.columns > .content')
    const available = source ? window.innerWidth - source.getBoundingClientRect().right - 24 : 0
    setPlacement(available >= 360 ? 'side' : 'bottom')
    setWidth(Math.min(620, Math.max(360, available)))
  }
  onMount(() => {
    updatePlacement()
    window.addEventListener('resize', updatePlacement)
    panel.focus({ preventScroll: true })
  })
  onCleanup(() => {
    window.removeEventListener('resize', updatePlacement)
    if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
      previousFocus.focus({ preventScroll: true })
    }
  })

  return <Portal>
    {/* Match md-dialog's visual scrim without making the reader inert or
        intercepting source clicks / dismissing an in-progress draft. */}
    <div class="card-drawer-scrim" aria-hidden="true" />
    <aside ref={panel} class="card-drawer edit-dialog" data-placement={placement()}
      style={{ '--card-drawer-width': `${width()}px` }} role="dialog" aria-modal="false"
      aria-labelledby={titleId} aria-busy={props.busy || undefined} tabIndex={-1}
      onKeyDown={event => {
        // Editing keys must not reach the review/reading shortcuts on window.
        event.stopPropagation()
        if (event.key === 'Escape' && !event.defaultPrevented && !props.busy) {
          event.preventDefault()
          props.onClose()
        }
      }}>
      <header class="card-drawer-header">
        <h2 id={titleId} class="md-typescale-title-large">{props.title}</h2>
        <md-icon-button aria-label={`关闭${props.title}`} disabled={props.busy} onClick={props.onClose}>
          <md-icon><IconClose /></md-icon>
        </md-icon-button>
      </header>
      <div class="card-drawer-body">{props.children}</div>
      <Show when={props.actions}>
        <footer class="card-drawer-actions">{props.actions}</footer>
      </Show>
    </aside>
  </Portal>
}
