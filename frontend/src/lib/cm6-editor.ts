// CodeMirror 6 source-mode markdown editor (user spec 2026-09-23).
// LAZY CHUNK: only imported via `await import()` from SegEditDialog, so
// CM6 (~180KB gzip) never lands in the main bundle — the review UI pays
// nothing until the user opens the segment editor.
//
// Source mode on purpose (NOT WYSIWYG/ProseMirror): the doc stays verbatim
// .md text, so the write-back diff is exactly the lines the user typed and
// segment line anchoring (start_line/fingerprint) never gets clobbered by
// serializer normalization. This is the same architectural choice Obsidian
// made for Live Preview (CM6 under the hood).
import { EditorState, Prec } from '@codemirror/state'
import {
  EditorView, keymap, lineNumbers, highlightActiveLine,
  highlightActiveLineGutter, drawSelection,
} from '@codemirror/view'
import { markdown, markdownLanguage } from '@codemirror/lang-markdown'
import {
  syntaxHighlighting, defaultHighlightStyle,
  bracketMatching, indentOnInput,
} from '@codemirror/language'
import { oneDarkHighlightStyle } from '@codemirror/theme-one-dark'
import { history, defaultKeymap, historyKeymap, indentWithTab } from '@codemirror/commands'
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'

export interface Cm6Handle {
  getText(): string
  /** replace the current selection (or insert at cursor) */
  insertAtCursor(text: string): void
  /** wrap the selection in marker chars (** for bold, * for italic…) */
  wrapSelection(marker: string): void
  /** editor scroll fraction (0..1) for one-way preview sync */
  scrollFraction(): number
  /** force a viewport/line-metrics re-measure (after container animations) */
  requestMeasure(): void
  focus(): void
  destroy(): void
}

export interface Cm6Options {
  initial: string
  dark: boolean
  /** debounced doc change → preview refresh */
  onUpdate?: () => void
  /** Mod-Enter → save */
  onSave?: () => void
  /** editor scrolled → one-way preview sync */
  onScroll?: (fraction: number) => void
}

// MD3-token theme: the editor chrome follows the app's light/dark scheme
// instead of shipping its own palette (oneDark would clash with the MD3
// baseline紫 surface tokens).
function mdTheme(dark: boolean) {
  return EditorView.theme({
    '&': {
      height: '100%',
      fontSize: '14px',
      backgroundColor: 'var(--md-sys-color-surface)',
      color: 'var(--md-sys-color-on-surface)',
    },
    '.cm-scroller': {
      overflow: 'auto',
      fontFamily: "ui-monospace, 'Noto Sans Mono CJK SC', 'Sarasa Mono SC', Menlo, Consolas, monospace",
      lineHeight: '1.65',
    },
    '.cm-content': { caretColor: 'var(--md-sys-color-primary)' },
    '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--md-sys-color-primary)' },
    '.cm-gutters': {
      backgroundColor: 'var(--md-sys-color-surface-container-low)',
      color: 'var(--md-sys-color-on-surface-variant)',
      border: 'none',
    },
    '.cm-activeLine': {
      backgroundColor: 'color-mix(in srgb, var(--md-sys-color-primary) 7%, transparent)',
    },
    '.cm-activeLineGutter': {
      backgroundColor: 'color-mix(in srgb, var(--md-sys-color-primary) 10%, transparent)',
    },
    '&.cm-focused': { outline: 'none' },
    '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {
      backgroundColor: 'var(--md-sys-color-secondary-container)',
    },
  }, { dark })
}

export function createMdEditor(parent: HTMLElement, opts: Cm6Options): Cm6Handle {
  // Prec.highest: defaultKeymap binds Mod-Enter → insertBlankLine; wrapping
  // our Mod-Enter → save at the highest precedence guarantees the save wins
  // regardless of extension registration order.
  const saveKeymap = Prec.highest(keymap.of([{
    key: 'Mod-Enter',
    run: () => { opts.onSave?.(); return true },
  }]))

  const state = EditorState.create({
    doc: opts.initial,
    extensions: [
      // saveKeymap FIRST: CM6 resolves keymaps by registration order, and
      // defaultKeymap binds Mod-Enter → insertBlankLine. Registering our
      // Mod-Enter → save ahead of it makes the save win (precedence).
      saveKeymap,
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightActiveLine(),
      drawSelection(),
      history(),
      closeBrackets(),
      keymap.of([
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...historyKeymap,
        ...searchKeymap,
        indentWithTab,
      ]),
      markdown({ base: markdownLanguage }),
      syntaxHighlighting(opts.dark ? oneDarkHighlightStyle : defaultHighlightStyle),
      bracketMatching(),
      indentOnInput(),
      highlightSelectionMatches(),
      EditorView.lineWrapping,
      mdTheme(opts.dark),
      EditorView.updateListener.of(u => {
        if (u.docChanged) opts.onUpdate?.()
      }),
      ...(opts.onScroll
        ? [EditorView.domEventHandlers({
            scroll: (_e, v) => {
              const d = v.scrollDOM
              const max = d.scrollHeight - d.clientHeight
              opts.onScroll!(max > 0 ? d.scrollTop / max : 0)
            },
          })]
        : []),
    ],
  })
  // root: document — CRITICAL (user bug 2026-09-23). CM6 defaults to
  // getRoot(parent), which walks up through assignedSlot/parentNode; inside
  // md-dialog's slotted light DOM this resolves to the dialog's SHADOW root,
  // so StyleModule.mount injects the base theme (.cm-scroller display:flex,
  // gutter position:sticky, …) into an adoptedStyleSheets of a tree the
  // editor is NOT in → zero styling: line numbers stack ABOVE the content,
  // the scroller can't scroll, everything misaligns. Pinning root to the
  // document mounts the styles where the editor actually renders.
  const view = new EditorView({ state, parent, root: document })

  return {
    getText: () => view.state.doc.toString(),
    insertAtCursor(text: string) {
      const { from, to } = view.state.selection.main
      view.dispatch({
        changes: { from, to, insert: text },
        selection: { anchor: from + text.length },
        scrollIntoView: true,
      })
      view.focus()
    },
    wrapSelection(marker: string) {
      const { from, to } = view.state.selection.main
      if (from === to) {
        // no selection: insert the markers and park the cursor between them
        this.insertAtCursor(marker + marker)
        const pos = from + marker.length
        view.dispatch({ selection: { anchor: pos } })
        return
      }
      const sel = view.state.sliceDoc(from, to)
      view.dispatch({
        changes: { from, to, insert: marker + sel + marker },
        selection: { anchor: from + marker.length, head: to + marker.length },
        scrollIntoView: true,
      })
      view.focus()
    },
    scrollFraction() {
      const d = view.scrollDOM
      const max = d.scrollHeight - d.clientHeight
      return max > 0 ? d.scrollTop / max : 0
    },
    requestMeasure: () => view.requestMeasure(),
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  }
}
