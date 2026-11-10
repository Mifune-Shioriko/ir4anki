import {render} from 'solid-js/web'
import {createSignal} from 'solid-js'
import '../src/theme'
import '../src/index.css'
import '../src/study.css'
// Vite alias selects the frozen g001 or g002 actual production component.
import {RelatedPanel} from '@page-related-panel'
const seed=JSON.parse(new URLSearchParams(location.search).get('seed')!)
function Probe(){
 const [chunk,setChunk]=createSignal(seed.root),[page,setPage]=createSignal({...seed.root,page:3}),[note,setNote]=createSignal<number|null>(null)
 const read=(source:typeof seed.root,n:number)=>{setNote(null);setChunk({...source});setPage({...source,page:n})}
 return <><nav aria-label="page peer fixture controls">
  <button onClick={()=>read(seed.root,3)}>A3</button><button onClick={()=>read(seed.root,4)}>A4</button><button onClick={()=>read(seed.root,2)}>A2</button><button onClick={()=>read(seed.root,5)}>A5</button>
  <button onClick={()=>read(seed.other.root,3)}>B3</button><button onClick={()=>read(seed.revised.root,3)}>R3</button>
  <button onClick={()=>{setChunk(null);setNote(seed.notes['4'].note_id)}}>Review4</button>
 </nav><RelatedPanel chunk={chunk()} pageSource={page()} noteId={note()} cardKey={note()}/></>
}
render(()=><Probe/>,document.getElementById('app')!)
