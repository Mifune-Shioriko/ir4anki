import { onMount, onCleanup, createSignal, Show } from 'solid-js'
import type { Cm6Handle } from '../lib/cm6-editor'
import { renderMarkdown } from '../lib/markdown'
import { api } from '../api'
import { AuthoringToolbar } from './AuthoringToolbar'
import { IconFormatBold, IconFormatItalic, IconFunctions, IconImage, IconGrid } from './icons'

export function MarkdownField(props: { initial: string; onChange: (text: string) => void; onReady?: (cm: Cm6Handle) => void; onSave?: () => void; label?: string; preview?: (text: string) => string }) {
 let host!: HTMLDivElement, file!: HTMLInputElement, cm: Cm6Handle | undefined
 let disposed = false
 const [text, setText] = createSignal(props.initial)
 const [error, setError] = createSignal('')
 onMount(async () => {
  try {
   const { createMdEditor } = await import('../lib/cm6-editor')
   if (disposed) return
   cm = createMdEditor(host, { initial: props.initial, dark: document.documentElement.classList.contains('dark'), onSave: props.onSave,
    onUpdate: () => { const v = cm!.getText(); setText(v); props.onChange(v) } })
   props.onReady?.(cm)
   requestAnimationFrame(() => cm?.requestMeasure())
  } catch(e) { setError((e as Error).message) }
 })
 onCleanup(() => { disposed = true; cm?.destroy() })
 const upload = async (f: File) => {
  try { const {filename} = await api.upload(f); cm?.insertAtCursor(`![](/media/${encodeURIComponent(filename)})`) }
  catch(e) { setError((e as Error).message) }
 }
 return <div class="markdown-field">
  <AuthoringToolbar label={`${props.label ?? '正文'}格式工具栏`}
   primary={[
    { label: '加粗', icon: () => <IconFormatBold />, run: () => cm?.wrapSelection('**') },
    { label: '斜体', icon: () => <IconFormatItalic />, run: () => cm?.wrapSelection('*') },
   ]}
   secondary={[
    { label: '插入公式', icon: () => <IconFunctions />, run: () => cm?.insertAtCursor('$公式$') },
    { label: '插入图片', icon: () => <IconImage />, run: () => file.click() },
    { label: '插入表格', icon: () => <IconGrid />, run: () => cm?.insertAtCursor('\n| A | B |\n|---|---|\n|  |  |\n') },
   ]}>
   <input ref={file} type="file" accept="image/*" style="display:none" onChange={e => { const f=e.currentTarget.files?.[0]; if(f) void upload(f); e.currentTarget.value='' }} />
  </AuthoringToolbar>
  <div ref={host} class="card-md-editor" role="group" aria-label={`${props.label ?? '正文'}源码编辑器`} onPaste={e => { const f=Array.from(e.clipboardData?.files ?? []).find(f => f.type.startsWith('image/')); if(f) { e.preventDefault(); void upload(f) } }} />
  <Show when={error()}><div class="edit-error" role="alert">{error()}</div></Show>
  <section class="authoring-preview" role="region" aria-label={`${props.label ?? '正文'}预览`}>
   <div class="authoring-preview-label md-typescale-label-medium">预览</div>
   <div class="note-body card-md-preview" innerHTML={props.preview ? props.preview(text()) : renderMarkdown(text())} />
  </section>
 </div>
}
