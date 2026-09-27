import { Component, Show, createSignal } from 'solid-js'
import { NotePanel } from './NotePanel'
import { ReadingPanel } from './ReadingPanel'
import { RelatedPanel } from './RelatedPanel'
import type { ReadingChunk } from '../types'

// Right-column tabs (2026-09-27 桌面重设计): THE side column, at every
// desktop width (统一双栏, user spec 2026-09-27 round 2 — the separate
// 相关卡片 left column and its 1500px breakpoint are retired). 相关卡片
// (cards made from the SAME note segment) gets its own tab next to 笔记, so
// nothing is width-gated into invisibility anymore.
//
// Both panes stay MOUNTED (display:none swap) so switching tabs doesn't
// refetch or lose scroll position. The reveal gate lives in the props App
// passes (noteId=null while face-down), identical to the old columns.
interface Props {
  /** reading round / trace detour: the chunk on screen (switches both
   *  panes to their reading variants) */
  chunk: ReadingChunk | null
  /** review mode: revealed card's note id (null until reveal — leak gate) */
  noteId: number | null
  cardKey: number | null
  blocked: boolean
  refreshToken: number
  noteReloadToken: unknown
}

export const SidePanelTabs: Component<Props> = (props) => {
  const [tab, setTab] = createSignal(0)
  const onTabChange = (e: Event) => {
    const t = e.currentTarget as HTMLElement & { activeTabIndex?: number }
    setTab(t.activeTabIndex ?? 0)
  }
  return (
    <div class="side-panel-tabs">
      <md-tabs class="note-tabs" activeTabIndex={tab()} onChange={onTabChange}>
        <md-primary-tab><span class="note-tab-label">笔记</span></md-primary-tab>
        <md-primary-tab><span class="note-tab-label">相关卡片</span></md-primary-tab>
      </md-tabs>
      <div
        class="side-panel-body"
        style={{ display: tab() === 0 ? 'flex' : 'none' }}
      >
        <Show
          when={props.chunk}
          fallback={
            <NotePanel
              noteId={props.noteId}
              cardKey={props.cardKey}
              blocked={props.blocked}
            />
          }
        >
          <ReadingPanel chunk={props.chunk!} reloadToken={props.noteReloadToken} />
        </Show>
      </div>
      <div
        class="side-panel-body"
        style={{ display: tab() === 1 ? 'flex' : 'none' }}
      >
        <Show
          when={props.chunk}
          fallback={
            <RelatedPanel
              noteId={props.noteId}
              cardKey={props.cardKey}
              blocked={props.blocked}
              refreshToken={props.refreshToken}
            />
          }
        >
          <RelatedPanel chunk={props.chunk!} refreshToken={props.refreshToken} />
        </Show>
      </div>
    </div>
  )
}
