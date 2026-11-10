import { Component, JSX, Show, onMount, onCleanup } from 'solid-js'

/** Actions keep native activation; page-level study shortcuts don't consume it. */
const containKeys=(event:KeyboardEvent)=>{if(event.key===' '||event.key==='Enter')event.stopPropagation()}
export const StudyToolbar: Component<{label:string;children:JSX.Element}> = props => (
 <div class="card-header-actions study-toolbar" role="toolbar" aria-label={props.label} onKeyDown={containKeys}>{props.children}</div>
)
export const StudyActionDock: Component<{children:JSX.Element}> = props => (
 <div class="action-area study-action-dock" onKeyDown={containKeys}><div class="ease-buttons">{props.children}</div></div>
)
/** Three actual layout rows: chrome, scrollable document, reserved action area. */
export const StudyCardShell: Component<{
 stage:'reading'|'review';toolbar:JSX.Element;source:string;
 badge?:boolean;dock?:JSX.Element;children:JSX.Element;lecture?:boolean;
 onScrollElement?:(element:HTMLDivElement|null)=>void;
}> = props => {
 let scroll:HTMLDivElement|undefined
 onMount(()=>props.onScrollElement?.(scroll!))
 onCleanup(()=>props.onScrollElement?.(null))
 return (
 <section class={`flashcard-wrapper stage-${props.stage} study-shell`} data-lecture-reading={props.lecture?'':undefined} aria-label={props.stage==='reading'?'当前阅读片段':'当前复习卡片'}>
  <header class="study-header">
   <div class="study-source-row md-typescale-label-small" aria-label="来源">
    <span class="study-kind">{props.stage==='reading'?'阅读片段':'复习'}</span>
    <span class="study-source" title={props.source}>{props.source}</span>
    <Show when={props.badge}><span class="info-chip info-chip--new">新卡</span></Show>
   </div>
   {props.toolbar}
  </header>
  <div class="study-scroll" ref={scroll}><div class="study-body">{props.children}</div></div>
  {props.dock}
 </section>
)
}
