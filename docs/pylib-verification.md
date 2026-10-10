# Native migration verification

## Verdict

Implementation and isolated verification are complete for the native adapter,
behavioral/UI test migration, durable single-slot undo and native capabilities,
raw Markdown authoring and operator deployment preparation. This is NOT a
production deployment verdict. Live switching and the assessment's one-week
observation/remote infrastructure retirement are still pending operator action.

## Executed evidence

- Official pinned engine: `anki==25.2.7`.
- Seven native/deployment pytest files: **39 passed**. Includes real temporary
  collections, exact card/FSRS/revlog restoration, leech tagging reversal while
  retaining later user field/tag edits, real independent loopback sync/media,
  application restart/round recovery, owner cancellation/shutdown and deployment
  preflight/snapshot/health checks.
- Full regression runner: **15 suites, 521 passing assertions, zero failures**.
  Fourteen suites use real disposable pylib fixtures; sync_throttle retains
  deliberate connect protocol/fault injection coverage.
- Raw Markdown real-Collection API tests: QA/cloze add/save/reopen/edit,
  tags and two native cloze ordinals passed. Native search result order is not
  assumed to match cloze ordinal order.
- Markdown/management browser test: CM6 raw QA save/edit/reopen, legacy HTML
  sanitization, cloze preview/native generation, engine confirmation/cancellation,
  backup/export contract and connect-mode navigation hiding passed.
- Frontend Markdown/ordinal/API/download routing tests passed.
- TypeScript project check and Vite production build passed, output isolated
  from the live frontend. Non-blocking Node experimental-loader/type-stripping
  warnings and Vite large-chunk warnings remain.
- `bash -n deploy/install.sh deploy/update.sh` and `git diff --check` passed.

## Review fixes and coverage

Independent Codex read-only source reviews identified and checked fixes for:

1. Native answer/journal crash window: wrap the native answer and fsynced
   after-state journal in `DBProxy.transact`, with SQLite commit last. Real
   process exits cover before journal persistence, after persistence/before
   SQLite commit and after SQLite commit/before acknowledgement.
2. Offline snapshot omitting undo authority: reject any non-null adjacent
   journal; never advertise such an incomplete snapshot as restorable.
3. Pending-answer backup downloaded after undo: artifact-specific safe provenance
   and SHA-256 binding. Serve exactly the verified backup bytes, not a mutable
   file path. Pending backups stay local recovery material.
4. Skipped backup promoting old provenance: preserve markers when the native
   backup reports no artifact was created.
5. Normal deletion and deletion/bookkeeping crash: journal the business round
   transition both in deletion and missing-card reconciliation. Real app tests
   cover two restarts and subsequent undo of the prior answer.
6. Scheduler note changes: store before/after note rows; restore unchanged rows
   exactly or invert scheduler tag deltas while preserving later user edits.

Final focused review found no blocker remaining in the reviewed fixes. Reviews
are source evidence, not substitutes for the separately executed tests. The
Hermes qwen reviewer failed to start due to a subscription/model mismatch and
provides no review evidence; the independent Codex CLI reviews do.

## Evidence locations in this workspace

- `/home/shioriko/.hermes/cache/scratch/ir4anki-verified-regression.json`
- `/home/shioriko/.hermes/cache/scratch/ir4anki-verified-regression/`
- `/home/shioriko/.hermes/cache/scratch/ir4anki-final-dist/`
- `/home/shioriko/.hermes/cache/scratch/ir4anki-final-review.txt`
- `/home/shioriko/.hermes/cache/scratch/ir4anki-final-review-followup.txt`
- `/home/shioriko/.hermes/cache/scratch/ir4anki-final-review-last.txt`
- `/home/shioriko/.hermes/cache/scratch/ir4anki-review-delete-crash.txt`

Scratch evidence is temporary. Durable test entry points and operator boundaries
are in `scripts/REGRESSION.md` and `docs/pylib-migration.md`.

## Not executed

No live service stop/restart, frontend bundle replacement, production collection
mutation, production-account sync, secret configuration, Beelink container removal
or one-week observation has been performed by these tests. Changes remain on
`feat/pylib-backend`; publication and live deployment are separate actions.
