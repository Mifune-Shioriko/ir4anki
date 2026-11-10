import type {Cm6Handle} from '../lib/cm6-editor'
import {lectureRequest,pageRequestPath,type Raster} from '../lib/lecture-api'
import {createSignal,onMount,Show} from 'solid-js'
import {api} from '../api'
import {MARKDOWN_TAG} from '../lib/cloze'
import {QuestionAnswerFields} from './QuestionAnswerFields'
import {CardDrawer} from './CardDrawer'
import type {PageLocator} from '../lib/lecture-api'
export function LectureQuestionAnswer(props:{source:PageLocator;path:string;questionSeed?:string;onClose:()=>void;onAdded:()=>void}){
 const handles=new Map<string,Cm6Handle>()
 const [crop,setCrop]=createSignal([0,0,1,1])
 const key=`ir4anki.qa.${props.source.document_id}.${props.source.revision_id}.${props.source.page}`
 const [fields,setFields]=createSignal<string[]>([]),[values,setValues]=createSignal<Record<string,string>>({}),[ready,setReady]=createSignal(false),[error,setError]=createSignal(''),[busy,setBusy]=createSignal(false)
 onMount(async()=>{try{const info=await api.addInfo();setFields(info.fields);let draft={};try{draft=JSON.parse(localStorage.getItem(key)??'{}')}catch{setError('无法读取本地草稿，请检查浏览器存储')}setValues(Object.keys(draft).length?draft:{[info.fields[0]]:props.questionSeed??''});setReady(true)}catch(e){setError((e as Error).message)}})
 function change(field:string,text:string){const next={...values(),[field]:text};setValues(next);try{localStorage.setItem(key,JSON.stringify(next))}catch{setError('无法保留本地草稿，请保存后再离开')}}
 async function save(){if(busy()||!ready())return;setBusy(true);setError('');try{const snapshot=JSON.stringify(values());await api.addCard(values(),[MARKDOWN_TAG],{...props.source,path:props.path,chunk_key:props.source.segment_id});if(localStorage.getItem(key)===snapshot)localStorage.removeItem(key);props.onAdded();props.onClose()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function screenshot(field:string){setBusy(true);setError('');try{const meta=await lectureRequest<Raster>(pageRequestPath(props.source,props.source.page)+'/raster');const result=await lectureRequest<{filename:string}>(pageRequestPath(props.source,props.source.page)+'/crop','POST',{rect:crop(),raster_hash:meta.raster_hash});handles.get(field)?.insertAtCursor(`![页面截图](/media/${encodeURIComponent(result.filename)})`)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <CardDrawer title="添加问答卡" onClose={props.onClose} busy={busy()}><section class="lecture-inline-qa" aria-label="课件问答编辑器">
  <div class="lecture-section-heading"><div><span class="lecture-eyebrow">手动问答 · 来源第 {props.source.page} 页</span><h2>把理解变成问题</h2><p class="lecture-help">左侧课件可继续翻阅；这张卡仍关联打开编辑器时的来源页。</p></div></div>
  <Show when={ready()}><details class="lecture-crop-controls"><summary>从来源第 {props.source.page} 页插入截图</summary><p class="lecture-help">页坐标中的百分比范围；原图像素与后端基准图一致。</p><div class="lecture-controls">{['左','上','宽','高'].map((label,i)=><label>{label}<input aria-label={`截图${label}百分比`} type="number" min="0" max="100" value={crop()[i]*100} onInput={e=>setCrop(v=>v.map((x,j)=>j===i?Number(e.currentTarget.value)/100:x))}/></label>)}{fields().map(field=><md-text-button disabled={busy()} onClick={()=>void screenshot(field)}>插入到{field}</md-text-button>)}</div></details><QuestionAnswerFields fields={fields()} initial={values()} onChange={change} onSave={()=>void save()} onReady={(field,cm)=>handles.set(field,cm)}/></Show>
  <Show when={error()}><p class="lecture-error" role="alert">{error()}</p></Show>
  <footer class="lecture-save-dock"><span>草稿留在此设备 · 明确保存才制卡</span><md-filled-button disabled={busy()||!ready()} onClick={()=>void save()}>保存问答卡</md-filled-button></footer>
 </section></CardDrawer>
}
