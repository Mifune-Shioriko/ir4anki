# Daily reading and review flow

Start deals reading and cards once. Each file still contributes at most two
reading segments; the existing gates, all-due/all-available-new card deal,
default 36/day release budget and next-day release of today's creations remain
unchanged. Reading has no FSRS or due-date scheduling.

For the actual dealt counts S reading slots and C cards, reading slot i is
followed by `floor(i*C/S) - floor((i-1)*C/S)` cards. Zero-card blocks go straight
to the next reading slot. With an empty side, only the other side runs.
`flow.json` persists policy `even-floor-v1`, original counts and block sizes,
queue identities, slot membership, reconciled cursor and summary. The reading
SQLite queue and native card queue/journal retain their independent engines.
`GET /api/flow/state` resumes the authoritative phase; start is idempotent while
a round is active. Legacy partial rounds adopt their remaining queues and
statistics; only a missing queue is dealt. Persisted initialization intent
recovers interrupted deals without replacing a committed queue.

Complete, skip and next consume a reading slot (with separate statistics).
Split children replace their parent's membership in the same slot: splitting
alone never starts its review block. The slot is consumed after its remaining
children are advanced or removed. Parent aliases make the split/queue write
boundary recoverable. Drift, deletion and list removal reconcile remaining
queue membership; missing cards count as handled, not answered. Phase changes
never finish/restart queues or request sync. Trace is a detour and does not
consume a slot; closing it refreshes the underlying phase.

Undo is available during reading and after completion as well as review,
including Ctrl/Cmd+Z. It restores the last graded card through the existing
native journal, reopens the corresponding review debt and corrects answer/new
answer counts without undoing reading. Native scheduling guards still cover
card rows, revlog, decks and scheduling configuration. Known authoring
metadata (`nextPos`, current note type and last-used deck/type hints) is excluded
so field/tag edits and card creation survive undo; old snapshots are normalized
to the same guard. Sync remains deferred while that native undo is pending.

The summary appears only after both queues drain and survives reload and undo.
It distinguishes completed, skipped and deferred reading, actual answers and
new-card answers. Legacy rounds without historical answer counters retain
their existing handled count; no missing historical new-answer data is invented.
“结束阅读” skips the remaining reading slots in this round and continues cards;
untouched segment statuses remain available for a future round. “退出本轮”
commits undo locally and clears both queues and the coordinator, with recoverable
exit intent and no forced sync. A fresh start replaces completed queues through
the existing native round transition; unresolved journals still fail closed.

Verification commands and RED/GREEN logs are recorded in the implementation
run directory. `scripts/interleave_policy_test.py` tests the exact sequence;
`interleave_integration_test.py` and `interleave_recovery_test.py` use disposable
official Anki collections and ASGI transport. `interleave_ui_test.py` exercises
Playwright on a disposable native server and requires `REGRESSION_DIST` pointing
to an isolated build. It fails visibly when loopback/browser setup is unavailable.
