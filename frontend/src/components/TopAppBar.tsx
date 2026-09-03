import { Component, Show } from 'solid-js'

// MD3 "small top app bar" (tokens: _md-comp-top-app-bar-small.scss):
// container = surface, height 64px, headline = title-large / on-surface.
// The library has no <md-top-app-bar> web component, so this is plain HTML
// styled strictly with sys tokens. Global stats live in an md-chip-set.
interface Props {
  due: number | null
  newTotal: number | null
  /** preview pool size; null when preview mode is off (chip hidden) */
  previewPool?: number | null
}

export const TopAppBar: Component<Props> = (props) => {
  return (
    <header class="top-bar">
      <span class="top-bar__title md-typescale-title-large">Anki</span>
      <md-chip-set class="top-bar__chips" aria-label="全局统计">
        <md-assist-chip
          label={props.due != null ? `待复习 ${props.due}` : '待复习 —'}
          disabled={props.due == null}
        />
        <md-assist-chip
          label={props.newTotal != null ? `新卡池 ${props.newTotal}` : '新卡池 —'}
          disabled={props.newTotal == null}
        />
        <Show when={props.previewPool != null}>
          <md-assist-chip
            label={props.previewPool != null ? `预览池 ${props.previewPool}` : '预览池 —'}
            disabled={props.previewPool == null}
          />
        </Show>
      </md-chip-set>
    </header>
  )
}
