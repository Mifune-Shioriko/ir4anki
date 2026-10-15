import { Component, JSX, Show } from 'solid-js'

/** Study chrome owns layout; content typography never owns sidebar scrolling. */
export const StudyToolbar: Component<{label: string; children: JSX.Element}> = props => (
  <div class="card-header-actions study-toolbar" role="toolbar" aria-label={props.label}
    onKeyDown={event => { if (event.key === ' ' || event.key === 'Enter') event.stopPropagation() }}>
    {props.children}
  </div>
)
export const StudyActionDock: Component<{children: JSX.Element}> = props => (
  <div class="action-area study-action-dock"><div class="ease-buttons">{props.children}</div></div>
)
export const StudyCardShell: Component<{
  stage: 'reading' | 'review'; toolbar: JSX.Element; source: string;
  badge?: boolean; dock?: JSX.Element; children: JSX.Element;
}> = props => (
  <div class={`flashcard-wrapper stage-${props.stage} study-shell`}>
    <md-elevated-card class="card-container"><div class="card-inner">
      <div class="card-header study-header">{props.toolbar}</div>
      <div class="study-source-row md-typescale-label-small" aria-label="来源">
        <Show when={props.source}><span class="study-source" title={props.source}>{props.source}</span></Show>
        <Show when={props.badge}><span class="info-chip info-chip--new">新卡</span></Show>
      </div>
      {props.children}
    </div></md-elevated-card>
    {props.dock}
  </div>
)
