import type {Cm6Handle} from '../lib/cm6-editor'
import {For} from 'solid-js'
import {MarkdownField} from './MarkdownField'
// Both legacy modal and lecture inline authoring use the same CM6/upload/math
// field and preview. Layout belongs to the enclosing form, never the side pane.
export const QuestionAnswerField=MarkdownField
export function QuestionAnswerFields(props:{fields:string[];initial:Record<string,string>;onChange:(field:string,value:string)=>void;onSave:()=>void;onReady?:(field:string,cm:Cm6Handle)=>void}){
 return <div class="lecture-qa-fields"><For each={props.fields}>{field=><section><h3>{field}</h3><QuestionAnswerField label={field} initial={props.initial[field]??''} onChange={text=>props.onChange(field,text)} onSave={props.onSave} onReady={cm=>props.onReady?.(field,cm)}/></section>}</For></div>
}
