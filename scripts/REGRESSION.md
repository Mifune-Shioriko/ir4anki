Run with the pinned dependency environment:

```bash
cd /tmp/ir4anki-remaining/tests
PATH=/tmp/node-v22.16.0-linux-x64/bin:$PATH npm --prefix frontend run build -- --configLoader runner --outDir /tmp/ir4anki-regression-dist
/tmp/ir4anki-dev/bin/python scripts/regression_runner.py --dist /tmp/ir4anki-regression-dist
```

The runner executes all 14 original owned suites plus `native_sync_deferral_test.py`, writes per-suite counts, exit codes, blockers and log paths to `/tmp/ir4anki-remaining/regression-results.json`, and exits nonzero if any suite fails. Use repeated `--suite NAME` to select suites; `--output`, `--timeout`, and `--dist` customize artifacts. No backend flag is provided: migrated behavioral fixtures only have a meaningful native path. `sync_throttle` explicitly selects connect for controlled protocol latency/fault assertions.

`native_fixture.py` creates temporary official Anki collections with Chinese QA/Cloze models, FSRS enabled, isolated corpus/state/media/backups, and cleared sync credentials. Historical note ids live in actual notes/cards tables. Seeded review scheduling uses native Collection before server ownership; scheduler answers use the real v3/FSRS engine. Runtime pool creation and assertions execute on the adapter owner thread. API suites explicitly close the client in `finally`; UI suites terminate only their own subprocess and clean temporary fixtures in `finally`. UI servers allocate unique loopback ports and verify `/api/status` reports healthy pylib before Playwright starts. Fixture identity is logged for every suite.

The reading UI suite retains its two original degraded/error scenarios with narrowly scoped Playwright HTTP failure routes; the healthy Cloze model endpoint is additionally checked through the native engine. The auto-release suite retains one explicit fail-soft exception scenario. These are intentional fault coverage, not replacement engines.

`native_sync_deferral_test.py` observes dispatch timings while all calls reach the real adapter. Unconfigured sync must fail closed, cooldown must defer/coalesce one attempt, and the collection must remain usable afterward. Transfer/media/full-sync validation remains in the existing disposable loopback integration:

```bash
/tmp/ir4anki-dev/bin/python -m pytest -q scripts/pylib_sync_test.py
```

This execution sandbox prohibits socket creation (`PermissionError: [Errno 1] Operation not permitted`). The seven UI suites and loopback integration therefore cannot reach their assertions here. They are recorded as failures/blockers, never skipped or reported green. Run them in a workspace permitting loopback sockets to finish UI validation. See `/tmp/ir4anki-remaining/tests-summary.md` for the executed migration ledger and Phase5 integration notes.

Post-trial presentation / latency verification (2026-10-10):

```bash
cd /home/shioriko/ir4anki
/home/shioriko/.hermes/cache/scratch/post-trial/venv/bin/python scripts/post_trial_verify.py --output /home/shioriko/.hermes/cache/scratch/post-trial/hermes-check-cm6
```

Use a Python environment containing the pinned backend dependencies, pytest and
Playwright. This session's scratch venv reuses the already installed local cache;
no served venv was changed. `post_trial_verify.py` strips inherited `ANKI_*`, puts
collection/corpus/state/media, subprocess temporary files, pytest artifacts,
screenshots and frontend output below Hermes scratch, and records exact commands
and exit codes in `checks.json`. The full runner and browser suites fail visibly
on socket/browser restrictions. `post_trial_ui_test.py` checks actual template
removal on both sides, legacy/Markdown cloze highlighting, CM6 dialog geometry,
last-line visibility, responsive stacking, raw mixed-source save/reopen and both
independent progress rings through undo/refresh/exhaustion. The original reading
UI expectations now require two labelled rings at the viewport bottom-right.
The latency probe uses deliberate 25ms/card delay reaching the real native
adapter; it is not a measurement of the installed service or network.

Hermes parent environment ran all checks successfully except the original
post-ui source assertion; this is distinct from this worker's socket blocker.
CM6 virtualizes `.cm-line` DOM nodes: source equality now uses the test-only
`cm6-browser-probe.mjs` public `EditorView.findFromDOM(...).state.doc` reader.
The reader bundle is built into the scratch UI run directory and supplied by a
Playwright route, never added to application globals or served frontend output.
Editor construction is awaited separately; exact expected text is not polled.
Exact add/update payloads, API fields, both reopened editor documents and API
rereads remain required. DOM assertions still cover geometry and last-line
visibility, including after reopening. `document-checks.json` records viewport,
rendered/document line counts and exact-source hashes for diagnosis.
Use the new output directory above to preserve the original parent evidence.

See [post-trial implementation, parent results and worker blocker](../docs/post-trial.md).
