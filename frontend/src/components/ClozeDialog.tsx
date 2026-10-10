import { Component, For, Show, createEffect, createSignal } from 'solid-js'
import { api } from '../api'
import { applyDialogGuard } from '../lib/dialog-guard'
import { MARKDOWN_TAG, clozeRanges, renderClozeMd } from '../lib/cloze'
import { MarkdownField } from './MarkdownField'
import type { Cm6Handle } from '../lib/cm6-editor'
import { IconPassword } from './icons'

// Raw Markdown cloze fields use the shared CM6 source editor. The storage
// tag is metadata only; no conversion or hidden source markers are added.
// Selection wrapping, hints and the existing split interaction stay intact.

interface Props {
  /** plain-text base for the 文字 field (whole chunk, selection pre-cloze'd) */
  initialText: string
  /** 渐进制卡 provenance link (null = plain add) */
  readingSource?: { path: string; chunk_key: string } | null
  onClose: () => void
  onAdded?: (noteId: number) => void
}

export const ClozeDialog: Component<Props> = (props) => {
  let clozeRef: Cm6Handle | undefined
  const [loading, setLoading] = createSignal(true)
  const [saving, setSaving] = createSignal(false)
  const [error, setError] = createSignal('')
  const [fieldNames, setFieldNames] = createSignal<string[]>([])
  // values[0] = the cloze 文字 field; values[1..] = plain extra fields
  const [values, setValues] = createSignal<string[]>([])
  const [tags, setTags] = createSignal<string[]>([])
  const [allTags, setAllTags] = createSignal<string[]>([])
  const [tagInput, setTagInput] = createSignal('')

  const [previewN, setPreviewN] = createSignal(1)
  const ordinals = () => [...new Set(clozeRanges(clozeText()).map(r => r.n))].sort((a,b) => a-b)
  const activeOrdinal = () => ordinals().includes(previewN()) ? previewN() : ordinals()[0]
  const clozeText = () => values()[0] ?? ''

  // seed immediately so the textarea is usable even if add/info fails
  // (dead AnkiConnect) — the field-name list stays empty and falls back to
  // 「文字」 in the label
  setValues([props.initialText])

  createEffect(() => {
    ;(async () => {
      try {
        setLoading(true)
        const [info, tagsData] = await Promise.all([api.addInfo('cloze'), api.tags()])
        const names = info.fields.length ? info.fields : ['文字']
        setFieldNames(names)
        setValues([props.initialText, ...names.slice(1).map(() => '')])
        setAllTags(tagsData.tags)
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setLoading(false)
      }
    })()
  })

  const setCloze = (v: string) => setValues(prev => [v, ...prev.slice(1)])

  const nextN = (): number => {
    const ns = clozeRanges(clozeText()).map(r => r.n)
    return ns.length ? Math.max(...ns) + 1 : 1
  }

  /** wrap the current textarea selection in {{cN::}} */
  const wrapCloze = () => {
    const ta = clozeRef
    if (!ta) return
    const s = ta.selection().from
    const e = ta.selection().to
    if (s === e) {
      setError('先在正文里选中要挖空的文字')
      return
    }
    const sel = clozeText().slice(s, e)
    // don't double-wrap: if the selection sits inside an existing cloze, bail
    const inside = clozeRanges(clozeText()).some(r => s >= r.start && e <= r.end)
    if (inside) {
      setError('这段已经在挖空里了')
      return
    }
    const n = nextN()
    const wrapped = `{{c${n}::${sel}}}`
    const next = clozeText().slice(0, s) + wrapped + clozeText().slice(e)
    setCloze(next)
    setError('')
    requestAnimationFrame(() => {
      ta.focus()
      ta.replaceRange(s,e,wrapped)
    })
  }

  /** unwrap the {{cN::}} marker around the cursor */
  const unwrapCloze = () => {
    const ta = clozeRef
    if (!ta) return
    const pos = ta.selection().from
    const ranges = clozeRanges(clozeText())
    const hit = ranges.find(r => pos >= r.start && pos <= r.end)
    if (!hit) {
      setError('把光标点进一个挖空里，再取消')
      return
    }
    const next = clozeText().slice(0, hit.start) + hit.content + clozeText().slice(hit.end)
    setCloze(next)
    setError('')
    requestAnimationFrame(() => {
      ta.focus()
      ta.replaceRange(hit.start,hit.end,hit.content)
    })
  }

  const onClozeKeyDown = (e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
      e.preventDefault()
      wrapCloze()
    }
  }

  // ---- tags (same UX as EditDialog) ----
  const addTag = (tag: string) => {
    const t = tag.trim()
    if (t && !tags().includes(t)) setTags(prev => [...prev, t])
    setTagInput('')
  }
  const removeTag = (tag: string) => setTags(prev => prev.filter(t => t !== tag))
  const filteredTags = () => {
    const q = tagInput().toLowerCase()
    if (!q) return []
    return allTags()
      .filter(t => t !== MARKDOWN_TAG && t.toLowerCase().includes(q) && !tags().includes(t))
      .slice(0, 5)
  }

  const clozeCount = () => clozeRanges(clozeText()).length

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      const names = fieldNames()
      const vals = values()
      const fields: Record<string, string> = {}
      names.forEach((n, i) => { fields[n] = vals[i] ?? '' })
      if (!clozeText().replace(/\s/g, '')) {
        setError('挖空内容不能为空')
        setSaving(false)
        return
      }
      if (clozeCount() === 0) {
        setError('至少要有一个 {{c1::…}} 挖空（选中文字点「挖空选中」）')
        setSaving(false)
        return
      }
      const res = await api.addCard(fields, [...tags(), MARKDOWN_TAG], props.readingSource ?? null, 'cloze')
      props.onAdded?.(res.noteId)
      props.onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <md-dialog
      class="edit-dialog cloze-dialog"
      open
      onClose={() => props.onClose()}
      ref={el => applyDialogGuard(el, () => props.onClose())}
    >
      <div slot="headline">添加挖空卡</div>

      <div slot="content" class="edit-dialog-content">
        <Show when={loading()}>
          <div class="edit-loading md-typescale-body-medium">加载中…</div>
        </Show>

        <Show when={!loading()}>
          <Show when={error()}>
            <div class="edit-error md-typescale-body-medium">{error()}</div>
          </Show>

          <div class="cloze-toolbar" role="toolbar" aria-label="挖空工具栏">
            <md-filled-tonal-button onClick={wrapCloze}>
              <md-icon><IconPassword /></md-icon>
              挖空选中
            </md-filled-tonal-button>
            <md-text-button onClick={unwrapCloze}>取消挖空</md-text-button>
            <span class="cloze-count md-typescale-label-medium">
              {clozeCount()} 处挖空 · 选中文字按 Ctrl/⌘+Shift+C
            </span>
          </div>

          <div class="field-group">
            <label class="field-label md-typescale-label-medium">
              {fieldNames()[0] ?? '文字'}（挖空正文）
            </label>
            <div onKeyDown={onClozeKeyDown}><MarkdownField label={fieldNames()[0] ?? '文字'} initial={clozeText()} onChange={setCloze} onReady={cm => { clozeRef = cm }} onSave={() => void handleSave()} preview={v => renderClozeMd(v,'a')} /></div>
          </div>

          <div class="cloze-toolbar cloze-ordinals" aria-label="预览挖空编号"><For each={ordinals()}>{n =>
            <md-text-button onClick={() => setPreviewN(n)} aria-pressed={activeOrdinal() === n}>c{n}</md-text-button>
          }</For></div>
          <div class="cloze-preview">
            <div class="cloze-preview-col authoring-preview" role="region" aria-label="正面预览">
              <div class="cloze-preview-label authoring-preview-label md-typescale-label-medium">正面（提问）</div>
              <div
                class="cloze-preview-box note-body md-typescale-body-medium"
                innerHTML={renderClozeMd(clozeText(), 'q', activeOrdinal())}
              />
            </div>
            <div class="cloze-preview-col authoring-preview" role="region" aria-label="背面预览">
              <div class="cloze-preview-label authoring-preview-label md-typescale-label-medium">背面（答案）</div>
              <div
                class="cloze-preview-box note-body md-typescale-body-medium"
                innerHTML={renderClozeMd(clozeText(), 'a', activeOrdinal())}
              />
            </div>
          </div>

          <For each={fieldNames().slice(1)}>
            {(name, i) => (
              <div class="field-group">
                <label class="field-label md-typescale-label-medium">{name}</label>
                <MarkdownField label={name} initial={values()[i()+1] ?? ''} onChange={v => setValues(prev => { const next=[...prev]; next[i()+1]=v; return next })} onSave={() => void handleSave()} />
              </div>
            )}
          </For>

          <div class="tags-section">
            <span class="field-label md-typescale-label-medium">标签</span>
            <Show when={tags().length > 0}>
              <md-chip-set class="tags-chips" aria-label="当前标签">
                <For each={tags()}>
                  {tag => <md-input-chip label={tag} onRemove={() => removeTag(tag)} />}
                </For>
              </md-chip-set>
            </Show>
            <md-outlined-text-field
              class="tag-input"
              label="添加标签"
              value={tagInput()}
              onInput={e => setTagInput(e.currentTarget.value)}
              onKeyDown={(e: KeyboardEvent) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addTag(tagInput())
                }
              }}
            />
            <Show when={filteredTags().length > 0}>
              <md-chip-set class="tag-suggestions" aria-label="标签建议">
                <For each={filteredTags()}>
                  {tag => <md-suggestion-chip label={tag} onClick={() => addTag(tag)} />}
                </For>
              </md-chip-set>
            </Show>
          </div>

          <div class="edit-hint md-typescale-body-small">
            正文支持 markdown（表格、加粗、$公式$），保存时保留 Markdown 原文；挖空标记 {'{{c1::答案}}'} 直接写在正文里（和 Anki 一样），可加提示 {'{{c1::答案::提示}}'}。
            同一编号的多处挖空会一起考。新卡进入预览池（挂起），预览放行后进入复习队列。
          </div>
        </Show>
      </div>

      <div slot="actions">
        <md-text-button onClick={() => props.onClose()}>取消</md-text-button>
        <md-filled-button onClick={handleSave} disabled={loading() || saving()}>
          添加挖空卡
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
