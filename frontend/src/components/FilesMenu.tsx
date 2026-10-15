import { For, createSignal } from 'solid-js'
import type { JSX } from 'solid-js'
import '@material/web/menu/menu.js'
import '@material/web/menu/menu-item.js'
import { CloseReason } from '@material/web/menu/menu'
import type { MdMenu, CloseMenuEvent } from '@material/web/menu/menu'
export function FilesMenu(props: { label: string; disabled?: boolean; actions: { label: string; run: () => void; icon?: () => JSX.Element }[]; text?: string }) {
  let trigger!: HTMLElement, menu!: MdMenu
  const [open, setOpen] = createSignal(false)
  return <span class="files-menu" onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
    <md-icon-button ref={trigger} class="files-row-action" touch-target="none" disabled={props.disabled}
      aria-label={props.label} title={props.label} aria-haspopup="menu" aria-expanded={open()}
      onClick={() => { trigger.focus(); menu.anchorElement = trigger; menu.skipRestoreFocus = false; void menu.show() }}>
      <md-icon><svg viewBox="0 -960 960 960" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M480-160q-33 0-56.5-23.5T400-240q0-33 23.5-56.5T480-320q33 0 56.5 23.5T560-240q0 33-23.5 56.5T480-160Zm0-240q-33 0-56.5-23.5T400-480q0-33 23.5-56.5T480-560q33 0 56.5 23.5T560-480q0 33-23.5 56.5T480-400Zm0-240q-33 0-56.5-23.5T400-720q0-33 23.5-56.5T480-800q33 0 56.5 23.5T560-720q0 33-23.5 56.5T480-640Z"/></svg></md-icon>
    </md-icon-button>
    {props.text && <span>{props.text}</span>}
    <md-menu ref={menu} positioning="popover" quick onOpening={() => setOpen(true)} onClosed={() => setOpen(false)}>
      <For each={props.actions}>{action => <md-menu-item on:close-menu={(event: CloseMenuEvent) => {
        const r = event.detail.reason
        if (r.kind === CloseReason.CLICK_SELECTION || (r.kind === CloseReason.KEYDOWN && 'key' in r && (r.key === 'Enter' || r.key === 'Space'))) {
          event.stopPropagation(); menu.skipRestoreFocus = true; menu.close(); action.run()
        }
      }}><div slot="headline">{action.label}</div>{action.icon && <md-icon slot="start">{action.icon()}</md-icon>}</md-menu-item>}</For>
    </md-menu>
  </span>
}
