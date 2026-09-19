import { Component, For, Show, createEffect, createSignal } from 'solid-js'
import { api } from '../api'
import { applyBottomSheetAnimation } from '../lib/bottom-sheet'
import { clozeMdToHtml, clozeRanges, renderClozeMd } from '../lib/cloze'
import { renderMarkdown } from '../lib/markdown'
import { IconPassword } from './icons'

// 挖空卡编辑框 (user spec 2026-09-19, round 2). Anki-native cloze: the
// 文字 field is a PLAIN TEXTAREA holding raw {{cN::content::hint}} markers
// (exactly how Anki's own editor shows them) — no contenteditable
// serialization to get wrong. Select text → 挖空选中 (or Ctrl/⌘+Shift+C)
// wraps it in the next {{cN::}}; 取消挖空 unwraps the marker around the
// cursor. Live 正面/背面 preview parses the markers (正面 shows […]/hint,
// 背面 reveals the content), math-rendered via KaTeX. Marker parsing lives
// in lib/cloze.ts — shared with the reading page's 已制卡片 list.
//
// Model + field names are data-driven from /api/card/add/info?kind=cloze
// (填空题: 文字 + 背面额外). The FIRST field is the cloze editor; any
// remaining fields render as plain textareas. Save → /api/card/add kind=cloze
// → preview pool suspended (same route as 问答题), provenance-linked to the
// reading chunk when opened from a reading round.

interface Props {
  /** plain-text base for the 文字 field (whole chunk, selection pre-cloze'd) */
  initialText: string
  /** 渐进制卡 provenance link (null = plain add) */
  readingSource?: { path: string; chunk_key: string } | null
  onClose: () => void
  onAdded?: (noteId: number) => void
}

export const ClozeDialog: Component<Props> = (props) => {
  let clozeRef: HTMLTextAreaElement | undefined
  const [loading, setLoading] = createSignal(true)
  const [saving, setSaving] = createSignal(false)
  const [error, setError] = createSignal('')
  const [fieldNames, setFieldNames] = createSignal<string[]>([])
  // values[0] = the cloze 文字 field; values[1..] = plain extra fields
  const [values, setValues] = createSignal<string[]>([])
  const [tags, setTags] = createSignal<string[]>([])
  const [allTags, setAllTags] = createSignal<string[]>([])
  const [tagInput, setTagInput] = createSignal('')

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
    const s = ta.selectionStart
    const e = ta.selectionEnd
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
      const pos = s + wrapped.length
      ta.setSelectionRange(pos, pos)
    })
  }

  /** unwrap the {{cN::}} marker around the cursor */
  const unwrapCloze = () => {
    const ta = clozeRef
    if (!ta) return
    const pos = ta.selectionStart
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
      const p = hit.start + Math.min(ta.selectionStart - hit.start, hit.content.length)
      ta.setSelectionRange(p, p)
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
      .filter(t => t.toLowerCase().includes(q) && !tags().includes(t))
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
      // STORE HTML (user spec 2026-09-20): Anki renders fields natively, so
      // the markdown source is converted (markers protected through the
      // conversion, $math$ → \(…\)) — the card looks exactly like the chunk
      // with a hole punched in it. The cloze field goes through
      // clozeMdToHtml; extra fields through plain markdown conversion (same
      // reason: literal \n and ** would show as junk otherwise).
      const clozeFieldName = names[0] ?? '文字'
      fields[clozeFieldName] = clozeMdToHtml(clozeText())
      names.slice(1).forEach((n, i) => {
        const v = vals[i + 1] ?? ''
        fields[n] = v.trim() ? renderMarkdown(v) : ''
      })
      const res = await api.addCard(fields, tags(), props.readingSource ?? null, 'cloze')
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
      ref={el => applyBottomSheetAnimation(el)}
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
            <textarea
              class="cloze-field md-typescale-body-medium"
              ref={clozeRef}
              value={clozeText()}
              onInput={e => setCloze((e.currentTarget as HTMLTextAreaElement).value)}
              onKeyDown={onClozeKeyDown}
              spellcheck={false}
            />
          </div>

          <div class="cloze-preview">
            <div class="cloze-preview-col">
              <div class="cloze-preview-label md-typescale-label-medium">正面（提问）</div>
              <div
                class="cloze-preview-box note-body md-typescale-body-medium"
                innerHTML={renderClozeMd(clozeText(), 'q')}
              />
            </div>
            <div class="cloze-preview-col">
              <div class="cloze-preview-label md-typescale-label-medium">背面（答案）</div>
              <div
                class="cloze-preview-box note-body md-typescale-body-medium"
                innerHTML={renderClozeMd(clozeText(), 'a')}
              />
            </div>
          </div>

          <For each={fieldNames().slice(1)}>
            {(name, i) => (
              <div class="field-group">
                <label class="field-label md-typescale-label-medium">{name}</label>
                <textarea
                  class="cloze-field cloze-field--short md-typescale-body-medium"
                  value={values()[i() + 1] ?? ''}
                  onInput={e => {
                    const v = (e.currentTarget as HTMLTextAreaElement).value
                    setValues(prev => {
                      const next = [...prev]
                      next[i() + 1] = v
                      return next
                    })
                  }}
                />
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
            正文支持 markdown（表格、加粗、$公式$），保存时自动转成 Anki 卡片格式；挖空标记 {'{{c1::答案}}'} 直接写在正文里（和 Anki 一样），可加提示 {'{{c1::答案::提示}}'}。
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
