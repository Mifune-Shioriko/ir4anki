// Browser-test-only reader. Never add application globals or serialize viewport
// DOM as source: CM6 deliberately virtualizes .cm-line nodes.
import { EditorView } from '@codemirror/view'

export function snapshot(element) {
  const view = EditorView.findFromDOM(element)
  if (!view) return null
  return {
    document: view.state.doc.toString(),
    documentLines: view.state.doc.lines,
    renderedLines: element.querySelectorAll('.cm-line').length,
    viewport: { ...view.viewport },
    visibleRanges: view.visibleRanges.map(range => ({ ...range })),
  }
}
