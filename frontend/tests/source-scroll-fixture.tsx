import { render } from 'solid-js/web'
import { createSignal, Show } from 'solid-js'
import '@material/web/tabs/tabs.js'
import '@material/web/tabs/primary-tab.js'
import '../src/index.css'
import '../src/tokens.css'
import { SidePanelTabs } from '../src/components/SidePanelTabs'
import { api } from '../src/api'
import { renderMarkdown } from '../src/lib/markdown'
import { invalidateDocuments } from '../src/lib/file-cache'
import data from 'virtual:source-scroll-data'
import type { ReadingChunk, ReadingSource } from '../src/types'
import './source-scroll-fixture.css'
const [config,setConfig]=createSignal({target:75,mode:'review',mounted:false,file:'r4ds',key:1})
const calls: unknown[]=[]
const segment=()=>data.segments.find(s=>s.seg_id===config().target)!
const path=()=>config().file==='r4ds'?data.path:'fixture-second.md'
const text=()=>config().file==='r4ds'?data.text:Array.from({length:700},(_,i)=>i%2?'':`Second file paragraph ${i+1}.`).join('\n')
const source=():ReadingSource=>({path:path(),seg_id:segment().seg_id,status:segment().status,stale:false,line_start:segment().start_line,line_end:segment().end_line,fingerprint:'synthetic-api-revision',edit_revision:'fixture',text:text().split('\n').slice(segment().start_line-1,segment().end_line).join('\n'),cards_created:segment().note_ids,breadcrumb:[]})
const chunk=():ReadingChunk=>({...source(),chunk_key:String(segment().seg_id),parent_seg_id:null,title:'R4DS actual source range',heading_path:[],status:segment().status as ReadingChunk['status'],ancestor_cards:[],file_chunks:0,file_done:0,file_skipped:0})
// Synthetic in-memory APIs only. Real parent components, FileViewer, cache, renderer.
api.readingSource=async (id)=>{calls.push({api:'source',id});return source()}
api.readingFile=async (p)=>{calls.push({api:'file',path:p});return {path:p,text:text()}}
api.readingCards=async ids=>{calls.push({api:'cards',ids});return {cards:[]}}
invalidateDocuments()
const dispose=render(()=> <div class="app-shell"><main class="app"><div class="columns"><div class="content"><h1>Offline mounted regression</h1><p>No application API or image network requests are allowed.</p><div style={{height:'1800px'}}>Outer scroll sentinel</div></div><aside class="note-column"><Show when={config().mounted}><SidePanelTabs chunk={config().mode==='reading'?chunk():null} noteId={segment().note_ids[0]} cardKey={config().key} blocked={false} refreshToken={0} noteReloadToken={0}/></Show></aside></div></main></div>,document.getElementById('app')!)
Object.assign(window,{fixture:{set:(p:Partial<ReturnType<typeof config>>)=>setConfig({...config(),...p}),state:()=>({...config(),segment:segment(),path:path()}),calls,imageURLs:[...new DOMParser().parseFromString(renderMarkdown(data.text),'text/html').querySelectorAll('img')].map(i=>i.src),tab:(index:number)=>{const tabs=document.querySelector('md-tabs') as HTMLElement & {activeTabIndex:number};tabs.activeTabIndex=index;tabs.dispatchEvent(new Event('change',{bubbles:true}))},unmount:()=>{setConfig({...config(),mounted:false})},dispose}})
