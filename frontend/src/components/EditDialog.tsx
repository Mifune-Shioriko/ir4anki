import { Component, For, Show, createEffect, createSignal, onMount } from 'solid-js'
import katex from 'katex'
import { api } from '../api'
import { applyBottomSheetAnimation } from '../lib/bottom-sheet'
import { fromEditorHtml, makeMathChip, makeMediaImg, mathifyField, toEditorHtml } from '../lib/rich'
import {
  IconFormatBold, IconFormatItalic, IconFormatUnderlined, IconInkHighlighter,
  IconImage, IconFormatClear, IconFunctions,
} from './icons'

// Rich-text note editor (MD3 bottom sheet). contenteditable fields are
// imperative by design: toolbar commands + paste-upload mutate the DOM
// directly, Solid only mounts the initial HTML (fresh mount per card —
// App.tsx unmounts the dialog on close).
//
// Two modes share the exact same UI:
//  - mode='edit' (default): loads the note behind props.cardId, saves via
//    /api/note/update.
//  - mode='add': blank fields from /api/card/add/info (问答题: 正面/背面),
//    saves via /api/card/add → the card enters the preview pool suspended
//    (same route as the add_cards.py pipeline).
//
// Math: fields store LaTeX delimiters (\(…\) inline / $$…$$ display, same
// convention as Flashcard's KaTeX auto-render). The editor turns them into
// atomic KaTeX-rendered chips (contenteditable=false); the fx toolbar button
// opens an inline editor panel with live KaTeX preview. Chips serialize back
// to delimiters on save, so Anki keeps plain LaTeX text.
//
// Media round-trip: bare Anki filenames <-> /media/ URLs via data-media,
// restored verbatim on save (rich.ts).
interface Props {
  cardId?: number
  mode?: 'edit' | 'add'
  /** 渐进制卡 provenance (2026-09-19): when adding from a reading round,
   * {path, chunk_key} of the source chunk — rides on /api/card/add so the
   * note id lands in the chunk's cards_created */
  readingSource?: { path: string; chunk_key: string } | null
  onClose: () => void
  onSaved?: (question: string, answer: string) => void
  onAdded?: (noteId: number) => void
}

export const EditDialog: Component<Props> = (props) => {
  let dialogRef: HTMLElement | undefined
  let texInputRef: any
  let imgFileRef: HTMLInputElement | undefined
  const [loading, setLoading] = createSignal(true)
  const [saving, setSaving] = createSignal(false)
  const [fields, setFields] = createSignal<Record<string, string>>({})
  const [tags, setTags] = createSignal<string[]>([])
  const [allTags, setAllTags] = createSignal<string[]>([])
  const [tagInput, setTagInput] = createSignal('')
  const [error, setError] = createSignal('')

  const isAdd = () => props.mode === 'add'

  // ---- imperative editor state (not reactive on purpose) ----
  const fieldRefs = new Map<string, HTMLDivElement>()
  let activeField: HTMLDivElement | undefined
  let savedRange: Range | null = null
  let mathEditChip: HTMLElement | null = null

  // ---- math panel state ----
  const [mathOpen, setMathOpen] = createSignal(false)
  const [mathTex, setMathTex] = createSignal('')
  const [mathDisplay, setMathDisplay] = createSignal(false)

  const fieldKeys = () => Object.keys(fields())

  onMount(() => {
    // CSS-based formatting (<span style=…>) instead of <b>/<i> tags — keeps
    // the stored HTML consistent across browsers
    document.execCommand('styleWithCSS', false, 'true')
  })

  createEffect(() => {
    ;(async () => {
      try {
        setLoading(true)
        if (isAdd()) {
          const [info, tagsData] = await Promise.all([api.addInfo(), api.tags()])
          setFields(Object.fromEntries(info.fields.map(f => [f, ''])))
          setTags([])
          setAllTags(tagsData.tags)
        } else {
          const [noteData, tagsData] = await Promise.all([api.note(props.cardId!), api.tags()])
          setFields(noteData.fields)
          setTags(noteData.tags)
          setAllTags(tagsData.tags)
        }
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setLoading(false)
      }
    })()
  })

  // ---- toolbar commands ----
  // pointerdown + preventDefault keeps the text selection inside the field
  const cmd = (name: string) => (e: PointerEvent) => {
    e.preventDefault()
    if (!activeField) return
    document.execCommand(name, false)
  }
  const hiliteDown = (e: PointerEvent) => {
    e.preventDefault()
    if (!activeField) return
    document.execCommand('hiliteColor', false, '#fdf3b0')
  }

  // ---- images ----
  const imgDown = (e: PointerEvent) => {
    e.preventDefault()
    if (!activeField) {
      alert('先点进一个文本框，再选择要插入的位置')
      return
    }
    const sel = window.getSelection()
    savedRange = sel?.rangeCount ? sel.getRangeAt(0).cloneRange() : null
    imgFileRef?.click()
  }

  const uploadInto = async (field: HTMLDivElement, file: File, range: Range | null) => {
    try {
      const { filename } = await api.upload(file)
      const img = makeMediaImg(filename)
      if (range && field.contains(range.commonAncestorContainer)) {
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(range)
        range.deleteContents()
        range.insertNode(img)
        range.setStartAfter(img)
        range.collapse(true)
        sel?.removeAllRanges()
        sel?.addRange(range)
      } else {
        field.appendChild(img)
      }
    } catch (err) {
      setError('图片上传失败：' + (err as Error).message)
    }
  }

  const onImgPicked = async (e: Event) => {
    const input = e.currentTarget as HTMLInputElement
    const f = input.files?.[0]
    input.value = ''
    if (!f || !activeField) return
    await uploadInto(activeField, f, savedRange)
  }

  const handlePaste = async (e: ClipboardEvent, key: string) => {
    const field = fieldRefs.get(key)
    if (!field) return
    const items = e.clipboardData?.items
    if (!items) return
    const files: File[] = []
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const f = item.getAsFile()
        if (f) files.push(f)
      }
    }
    if (!files.length) {
      // plain-text paste flows through natively; convert pasted math
      // delimiters (if any) into chips afterwards
      setTimeout(() => mathifyField(field), 0)
      return
    }
    e.preventDefault()
    const sel = window.getSelection()
    const range = sel?.rangeCount && field.contains(sel.getRangeAt(0).commonAncestorContainer)
      ? sel.getRangeAt(0).cloneRange()
      : null
    for (const f of files) await uploadInto(field, f, range)
  }

  // ---- math panel ----
  const getSelectedChip = (): HTMLElement | null => {
    const sel = window.getSelection()
    if (!sel || !sel.rangeCount || !activeField) return null
    const r = sel.getRangeAt(0)
    const node = r.commonAncestorContainer
    const el = node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement
    const byAncestor = el?.closest?.('.math-tex')
    if (byAncestor && activeField.contains(byAncestor)) return byAncestor as HTMLElement
    const frag = r.cloneContents()
    const selected = frag.querySelector('.math-tex')
    if (selected && activeField.contains(selected)) return selected as HTMLElement
    return null
  }

  const openMathPanel = (chip: HTMLElement | null) => {
    if (!activeField) {
      alert('先点进一个文本框，再选择要插入的位置')
      return
    }
    mathEditChip = chip
    setMathTex(chip?.getAttribute('data-tex') ?? '')
    setMathDisplay(chip?.getAttribute('data-display') === '1')
    if (!chip) {
      const sel = window.getSelection()
      savedRange = sel?.rangeCount ? sel.getRangeAt(0).cloneRange() : null
    }
    setMathOpen(true)
    requestAnimationFrame(() => texInputRef?.focus?.())
  }

  const mathDown = (e: PointerEvent) => {
    e.preventDefault()
    openMathPanel(getSelectedChip())
  }

  // clicking a chip selects it and opens the panel in edit mode
  const handleFieldClick = (e: MouseEvent, key: string) => {
    const target = (e.target as Element).closest?.('.math-tex') as HTMLElement | null
    if (!target) return
    activeField = fieldRefs.get(key)
    const sel = window.getSelection()
    const r = document.createRange()
    r.selectNode(target)
    sel?.removeAllRanges()
    sel?.addRange(r)
    openMathPanel(target)
  }

  const closeMath = () => {
    setMathOpen(false)
    mathEditChip = null
  }

  const commitMath = () => {
    const tex = mathTex().trim()
    const chip = mathEditChip
    if (!tex) { closeMath(); return }
    if (chip) {
      chip.setAttribute('data-tex', tex)
      chip.setAttribute('data-display', mathDisplay() ? '1' : '0')
      chip.innerHTML = katex.renderToString(tex, { throwOnError: false, displayMode: mathDisplay() })
      closeMath()
      return
    }
    const field = activeField
    if (!field) { closeMath(); return }
    field.focus()
    const sel = window.getSelection()
    if (savedRange && field.contains(savedRange.commonAncestorContainer)) {
      sel?.removeAllRanges()
      sel?.addRange(savedRange)
    } else {
      const end = document.createRange()
      end.selectNodeContents(field)
      end.collapse(false)
      sel?.removeAllRanges()
      sel?.addRange(end)
    }
    const r = sel!.getRangeAt(0)
    r.deleteContents()
    const newChip = makeMathChip(tex, mathDisplay())
    r.insertNode(newChip)
    r.setStartAfter(newChip)
    r.collapse(true)
    sel?.removeAllRanges()
    sel?.addRange(r)
    closeMath()
  }

  const mathPreview = () => {
    const tex = mathTex().trim()
    if (!tex) return '<span class="math-preview-empty">输入 LaTeX 后这里实时预览</span>'
    return katex.renderToString(tex, { throwOnError: false, displayMode: mathDisplay() })
  }

  // ---- tags ----
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

  // ---- save ----
  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      const out: Record<string, string> = {}
      fieldRefs.forEach((el, name) => {
        out[name] = fromEditorHtml(el.innerHTML)
      })
      if (isAdd()) {
        if (!Object.values(out).some(v => v.replace(/<[^>]*>/g, '').trim())) {
          setError('卡片内容不能为空')
          setSaving(false)
          return
        }
        const res = await api.addCard(out, tags(), props.readingSource ?? null)
        props.onAdded?.(res.noteId)
      } else {
        const res = await api.updateNote(props.cardId!, out, tags())
        props.onSaved?.(res.question, res.answer)
      }
      props.onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <md-dialog
      class="edit-dialog"
      open
      onClose={() => props.onClose()}
      ref={el => {
        dialogRef = el
        // Must be set before the dialog's open animation runs (the open
        // attribute triggers show() after a connected microtask).
        applyBottomSheetAnimation(el)
      }}
    >
      <div slot="headline">{isAdd() ? '添加卡片' : '编辑卡片'}</div>

      <div slot="content" class="edit-dialog-content">
        <Show when={loading()}>
          <div class="edit-loading md-typescale-body-medium">加载中…</div>
        </Show>

        <Show when={!loading()}>
          <Show when={error()}>
            <div class="edit-error md-typescale-body-medium">{error()}</div>
          </Show>

          <div class="edit-toolbar" role="toolbar" aria-label="格式工具栏">
            <md-icon-button aria-label="加粗" onPointerDown={cmd('bold')}>
              <md-icon><IconFormatBold /></md-icon>
            </md-icon-button>
            <md-icon-button aria-label="斜体" onPointerDown={cmd('italic')}>
              <md-icon><IconFormatItalic /></md-icon>
            </md-icon-button>
            <md-icon-button aria-label="下划线" onPointerDown={cmd('underline')}>
              <md-icon><IconFormatUnderlined /></md-icon>
            </md-icon-button>
            <span class="tool-sep" />
            <md-icon-button aria-label="高亮" onPointerDown={hiliteDown}>
              <md-icon><IconInkHighlighter /></md-icon>
            </md-icon-button>
            <span class="tool-sep" />
            <md-icon-button aria-label="插入公式" onPointerDown={mathDown}>
              <md-icon><IconFunctions /></md-icon>
            </md-icon-button>
            <md-icon-button aria-label="插入图片" onPointerDown={imgDown}>
              <md-icon><IconImage /></md-icon>
            </md-icon-button>
            <span class="tool-sep" />
            <md-icon-button aria-label="清除格式" onPointerDown={cmd('removeFormat')}>
              <md-icon><IconFormatClear /></md-icon>
            </md-icon-button>
            <input type="file" ref={imgFileRef} accept="image/*" style="display:none" onChange={onImgPicked} />
          </div>

          <Show when={mathOpen()}>
            <div class="math-panel">
              <div class="math-panel-head">
                <span class="field-label md-typescale-label-medium">
                  {mathEditChip ? '编辑公式' : '插入公式'}
                </span>
                <label class="math-mode">
                  <span class="math-mode-label md-typescale-label-medium">块级显示</span>
                  <md-switch
                    selected={mathDisplay()}
                    onChange={(e: Event) => setMathDisplay((e.currentTarget as any).selected)}
                  />
                </label>
              </div>
              <md-outlined-text-field
                ref={texInputRef}
                class="math-tex-input"
                label="LaTeX 公式"
                rows={3}
                value={mathTex()}
                onInput={(e: InputEvent) => setMathTex((e.currentTarget as HTMLInputElement).value)}
                onKeyDown={(e: KeyboardEvent) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault()
                    commitMath()
                  }
                }}
              />
              <div class="math-preview" innerHTML={mathPreview()} />
              <div class="math-actions">
                <md-text-button onClick={closeMath}>取消</md-text-button>
                <md-filled-button onClick={commitMath}>{mathEditChip ? '更新' : '插入'}</md-filled-button>
              </div>
            </div>
          </Show>

          <div class="edit-fields">
            <For each={fieldKeys()}>
              {key => (
                <div class="field-group">
                  <label class="field-label md-typescale-label-medium">{key}</label>
                  <div
                    class="rich-field"
                    contentEditable={true}
                    innerHTML={toEditorHtml(fields()[key])}
                    ref={(el: HTMLDivElement) => fieldRefs.set(key, el)}
                    onFocus={() => { activeField = fieldRefs.get(key) }}
                    onPaste={e => handlePaste(e, key)}
                    onClick={e => handleFieldClick(e, key)}
                  />
                </div>
              )}
            </For>
          </div>

          <div class="tags-section">
            <span class="field-label md-typescale-label-medium">标签</span>
            <Show when={tags().length > 0}>
              <md-chip-set class="tags-chips" aria-label="当前标签">
                <For each={tags()}>
                  {tag => (
                    <md-input-chip label={tag} onRemove={() => removeTag(tag)} />
                  )}
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
            {isAdd()
              ? '富文本编辑：格式、公式、图片、标签都会原样写回 Anki。新卡进入预览池（挂起），预览放行后进入复习队列。'
              : '富文本编辑：格式、公式、图片、标签都会原样写回 Anki，保存后立即生效。'}
          </div>
        </Show>
      </div>

      <div slot="actions">
        <md-text-button onClick={() => props.onClose()}>取消</md-text-button>
        <md-filled-button onClick={handleSave} disabled={loading() || saving()}>
          {isAdd() ? '添加' : '保存'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
