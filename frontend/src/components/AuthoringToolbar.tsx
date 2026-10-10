import { For, createSignal } from 'solid-js'
import type { JSX } from 'solid-js'
import '@material/web/menu/menu.js'
import '@material/web/menu/menu-item.js'
import { CloseReason } from '@material/web/menu/menu'
import type { MdMenu, CloseMenuEvent } from '@material/web/menu/menu'

export interface AuthoringAction {
  label: string
  icon: () => JSX.Element
  run: () => void
}

// Field-local actions, not a page-level docked/floating toolbar. The installed
// Material menu owns arrow keys, Escape, dismissal and viewport positioning.
export function AuthoringToolbar(props: {
  label: string
  primary: AuthoringAction[]
  secondary: AuthoringAction[]
  preserveHtmlSelection?: boolean
  children?: JSX.Element
}) {
  let trigger!: HTMLElement
  let menu!: MdMenu
  let savedSelection: Range | undefined
  const [expanded, setExpanded] = createSignal(false)
  const captureSelection = () => {
    if (!props.preserveHtmlSelection) return
    const selection = window.getSelection()
    if (!selection?.rangeCount) return
    const range = selection.getRangeAt(0)
    const node = range.commonAncestorContainer
    const element = node instanceof Element ? node : node.parentElement
    if (element?.closest('.rich-field')) savedSelection = range.cloneRange()
  }
  const restoreSelection = () => {
    if (!savedSelection || !savedSelection.commonAncestorContainer.isConnected) return
    const node = savedSelection.commonAncestorContainer
    const element = node instanceof Element ? node : node.parentElement
    const field = element?.closest<HTMLElement>('.rich-field')
    if (!field) return
    field.focus()
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(savedSelection)
  }
  const runInline = (action: AuthoringAction) => {
    captureSelection()
    restoreSelection()
    action.run()
  }
  const openMenu = () => {
    captureSelection()
    trigger.focus()
    menu.anchorElement = trigger
    menu.skipRestoreFocus = false
    void menu.show()
  }
  const runFromMenu = (action: AuthoringAction) => {
    // Closing must not steal focus back from the source or formula editor.
    menu.skipRestoreFocus = true
    menu.close()
    restoreSelection()
    action.run()
  }
  const button = (action: AuthoringAction) => <md-icon-button
    aria-label={action.label} title={action.label}
    onPointerDown={(e: PointerEvent) => { e.preventDefault(); captureSelection() }}
    onClick={() => runInline(action)}
  ><md-icon>{action.icon()}</md-icon></md-icon-button>

  return <div class="authoring-toolbar-shell">
    <div class="edit-toolbar" role="toolbar" aria-label={props.label}>
      <div class="authoring-tool-group" role="group" aria-label="文字格式">
        <For each={props.primary}>{button}</For>
      </div>
      <span class="tool-sep authoring-secondary" aria-hidden="true" />
      <div class="authoring-tool-group authoring-secondary" role="group" aria-label="更多格式">
        <For each={props.secondary}>{button}</For>
      </div>
      <md-icon-button class="authoring-overflow" ref={trigger}
        aria-label="更多格式" title="更多格式" aria-haspopup="menu" aria-expanded={expanded()}
        onPointerDown={(e: PointerEvent) => { e.preventDefault(); captureSelection() }}
        onClick={openMenu}
      ><md-icon><svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true">
        <circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" />
      </svg></md-icon></md-icon-button>
      <md-menu class="authoring-menu" ref={menu} positioning="popover" quick
        onOpening={() => setExpanded(true)} onClosed={() => setExpanded(false)}>
        <For each={props.secondary}>{action => <md-menu-item
          aria-label={action.label} on:close-menu={(event: CloseMenuEvent) => {
            const reason = event.detail.reason
            // Material's default li menuitem emits close-menu for keyboard
            // selection, not a click. Handle both once; Escape only dismisses.
            if (reason.kind === CloseReason.CLICK_SELECTION ||
                (reason.kind === CloseReason.KEYDOWN && 'key' in reason &&
                 (reason.key === 'Enter' || reason.key === 'Space'))) {
              event.stopPropagation()
              runFromMenu(action)
            }
          }}>
          <md-icon slot="start">{action.icon()}</md-icon>
          <div slot="headline">{action.label}</div>
        </md-menu-item>}</For>
      </md-menu>
      {props.children}
    </div>
  </div>
}
