# Card authoring drawer

`CardDrawer` is a non-modal supporting pane for adding/editing notes and creating
text clozes. A visual-only scrim matches the current-segment editor's depth;
`pointer-events: none` keeps the source interactive, with no focus trap or inert
reader. Clicking/scrolling the source does not dismiss it. Close, Cancel or Escape dismiss the editor, as before; unsaved
change confirmation is not added by this change.

- The reader retains its horizontal position and width when editing opens.
  If at least 360px is available to its right (after a 24px gap), the editor uses
  that space, capped at 620px. The editor floats above the supporting sidebar,
  inset 12px from the viewport edges with rounded corners. The
  sidebar stays rendered underneath: no hiding, unmounting or layout replacement.
- If the right-hand space is insufficient, including on tablets, the reader
  occupies the upper 48dvh and editing the lower 52dvh, with independent scrolling.
  Placement is recalculated on window resize; the reader is never squeezed.
- Opening uses the current-segment editor's Material dialog timing: 500ms,
  `cubic-bezier(.3, 0, 0, 1)`, a 50px downward slide (upward on bottom panes),
  staged content/actions fades, and its actual 32% scrim (using the same
  `--md-sys-color-scrim` color, fading in linearly over 500ms). The reference
  md-dialog has no edge shadow; the former approximate level-3 shadow is removed.
  Reduced-motion mode disables all entry movement/fades but retains the scrim.
- Global study shortcuts are paused while a drawer is open. Reader controls and
  local PDF page navigation remain usable. Saving is guarded against repeated
  keyboard submissions; closing is disabled while a save is in progress.

## Verification

Run from `frontend/`, with Playwright available:

```bash
node --loader ./tests/loader.mjs --test tests/*.test.mjs
node tests/card-drawer-browser.mjs
```

For an externally installed Playwright, set `PLAYWRIGHT_MODULE` to its absolute
`index.mjs`; optionally set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to an installed
browser. The runner builds a temporary production fixture and starts its own
loopback preview server, with **no production API proxy**. Every API request is
intercepted. Screenshots default to `/tmp/ir4anki-card-drawer-browser`.

The fixture covers 390/600/768/1024/1280/1440/1920px, add/edit/cloze, source
interaction and scrolling, unchanged reader width/position, non-overlapping source
geometry, menu Escape, retained sidebar visibility/geometry, overlay hit-testing
and single-request saving. Four additional cases check normal/reduced motion on
wide/narrow layouts, seeking animations to 0/250/500ms to verify movement, scrim
fade and unchanged source geometry. Four more cases open the actual SegEditDialog
and compare its scrim color/opacity and absence of edge shadow in light/dark
mode on wide/narrow screens. It uses synthetic text only.

## Separate lecture release

Some local installations run a separate release with `LectureReadingCard` and
`LectureQuestionAnswer`, which this Git checkout does not contain. Building this
checkout must not replace that release's frontend: doing so removes lecture UI.
The common drawer must also be integrated into the lecture source and verified
there before switching the running frontend.

Lecture integration keeps `PdfSourceViewer` mounted and active when opening a
question/answer drawer. Capture the source locator at drawer-open time; navigating
the reader must not retarget a draft or its crop/provenance. Closing the drawer
must retain the reader's current page. Image-occlusion authoring is not migrated
by this change.
