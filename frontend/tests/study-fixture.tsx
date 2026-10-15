import { render } from 'solid-js/web'
import { createSignal, Show } from 'solid-js'
import { syncThemeColor } from '../src/theme'
syncThemeColor()
import '../src/index.css'
import { ReadingCard } from '../src/components/ReadingCard'
import { Flashcard } from '../src/components/Flashcard'
import { ActionArea } from '../src/components/ActionArea'
import { NavRail } from '../src/components/NavRail'
import { MarkdownField } from '../src/components/MarkdownField'
import type { Card, ReadingChunk } from '../src/types'
const text = '# Shared heading\n\nSelectable source sentence.\nSecond source line.\n\n---\n\n> Quote\n\n- List entry\n\n| Head | Value |\n| --- | --- |\n| Cell | Value |\n\n```js\nconst answer = 42\n```\n\n$$x^2$$\n\nInline $x^2$ and $\\frac{1}{x^2+1}$.\n\n$$\\frac{x^2+1}{y_2}$$\n\n![synthetic](/synthetic-image.svg)'
const calls: unknown[] = []
const call = (name:string,...args:unknown[]) => calls.push({name,args})
const [config,setConfig]=createSignal({mode:'reading',metadata:'normal',busy:false,undo:true,source:true,revealed:false,legacy:false,cloze:false,ord:1,cardId:1,isNew:true,status:'active' as ReadingChunk['status'],long:false,sourceVersion:0})
const [revealed,setRevealed]=createSignal(false)
const [section,setSection]=createSignal<'study'|'files'|'readingList'>('study')
const metadata=()=>config().metadata==='empty'?'':config().metadata==='long'?'Long source › '.repeat(40):'Synthetic source › Heading'
const sourceText=()=>config().sourceVersion?'# Changed heading\n\nChanged source sentence.\n\n---':text
const longText=()=>text+'\n\n'+Array.from({length:100},(_,i)=>`Long paragraph ${i} for scrolling.`).join('\n\n')
const chunk=():ReadingChunk=>({path:metadata(),chunk_key:'synthetic-'+config().sourceVersion,seg_id:config().source?1:null,parent_seg_id:null,title:'Synthetic',heading_path:[],line_start:1,line_end:30,text:config().long?longText():sourceText(),status:config().status,cards_created:[],ancestor_cards:[],file_chunks:1,file_done:0,file_skipped:0})
const card=():Card=>({cardId:config().cardId,noteId:config().cardId,kind:config().cloze?'cloze':'qa',fields:{Front:config().cloze?'{{c1::other}} and {{c2::preserved::hint}}':text,Back:config().long?longText():text+'\n\nAnswer for card '+config().cardId},tags:config().legacy?[]:['ir4anki::markdown'],ord:config().ord,question:'<h1>Shared heading</h1><p>Legacy <span class="cloze" data-ordinal="2">[…]</span></p><hr><p>Source rule</p>',answer:'<hr id="answer"><h1>Legacy answer</h1><p>Card '+config().cardId+'</p><hr><p>Source rule</p>',deckName:metadata(),modelName:'Synthetic QA',css:'',isNew:config().isNew,due:0})
Object.assign(window,{fixture:{calls,set:(value:Partial<ReturnType<typeof config>>)=>{calls.length=0;setConfig({...config(),...value});if ('revealed' in value) setRevealed(!!value.revealed)},state:()=>({config:config(),revealed:revealed(),section:section()}),card,chunk,text,clear:()=>{calls.length=0}}})
window.addEventListener('keydown',e=>{if(e.key===' '||e.key==='Enter')call('page-shortcut')})
render(()=> <div class="app-shell"><button id="neutral" style={{position:'fixed',bottom:'0',left:'0','z-index':'20'}}>Keyboard start</button><NavRail section={section()} onNavigate={s=>{setSection(s);call('navigate',s)}} readingListSize={2}/>
<main class="app"><div class="columns"><div class="content" style={config().mode==='pair'?{display:'grid','grid-template-columns':'repeat(2,minmax(0,1fr))',gap:'24px','max-width':'1240px',flex:'1'}:{}}>
<Show when={['reading','trace','pair'].includes(config().mode)}><ReadingCard chunk={chunk()} busy={config().busy} variant={config().mode==='trace'?'trace':'round'} onComplete={()=>call('complete')} onNext={()=>call('next')} onSkip={()=>call('skip')} onAdd={()=>call('reading-add')} onCloze={(s,b)=>call('cloze',s,b)} onSplit={s=>call('split',s)} onEdit={()=>call('reading-edit')} onExit={()=>call('exit-reading')} onUndo={()=>call('reading-undo')} undoEnabled={config().undo} undoBusy={config().busy} onExitRound={()=>call('exit-round')} exitDisabled={config().busy} onBack={()=>call('back')}/></Show>
<Show when={['review','pair'].includes(config().mode)}><Flashcard card={card()} revealed={revealed()} traceAvailable={config().source} onTrace={()=>call('trace')} onEdit={()=>call('review-edit')} onUndo={()=>call('review-undo')} undoEnabled={config().undo} undoBusy={config().busy} onAdd={()=>call('review-add')} onDelete={()=>call('delete-confirmation')} dock={<ActionArea revealed={revealed()} answering={config().busy} onReveal={()=>{call('reveal');setRevealed(true)}} onAnswer={n=>call('answer',n)}/>} /></Show>
<Show when={config().mode==='review'}><div class="flow-actions"><md-text-button disabled={config().busy} onClick={()=>call('exit-round')}>退出本轮</md-text-button></div></Show>
<Show when={config().long}><div data-fixture-tail style={{height:'1500px'}}>Synthetic post-study scroll space</div></Show>
<Show when={config().mode==='authoring'}><div class="edit-dialog"><MarkdownField label="Synthetic authoring" initial={text} onChange={v=>call('authoring',v)}/></div></Show>
</div><Show when={window.innerWidth>=1180 && config().mode!=='pair'}><aside class="note-column">Synthetic supporting pane</aside></Show></div></main></div>,document.getElementById('root')!)
