# Daily reading and review flow

Start deals reading and cards once. Each file still contributes at most two
reading segments; the existing gates, all-due/all-available-new card deal,
default 36/day release budget and next-day release of today's creations remain
unchanged. Reading has no FSRS or due-date scheduling.

For S original reading slots and C effective eligible cards, reading slot i
is followed by `floor(i*C/S) - floor((i-1)*C/S)` recalls. C is the explicit
round answer count plus remaining eligible pending cards, recalculated after
withdrawals and undo. A successful answer, including Again, consumes one round
position regardless of its subsequent native learning schedule. Zero-card blocks go straight
to the next reading slot. With an empty side, only the other side runs.
`flow.json` persists policy `even-floor-v1`, original identities/counts and recalculated block sizes,
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
queue membership. `card_handled` retains the original-position diagnostic,
but missing/suspended/out-of-scope cards never earn recall credit. The review
ring uses `card_answered / effective_card_count`; pacing uses actual answers
against `floor(consumed*C/S)`. For 13 slots, 164 originals, 45 withdrawals,
2 consumed slots and 0 answers, this is REVIEW with target 18 and progress
0/119. Both queues reconcile before selecting whether to render a recall card,
including the first response after restoring persisted state. Withdrawn cards
are not automatically redealt if later unsuspended; a subsequent round or an
explicit recovered pending queue can include them again. Phase changes
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
new-card answers. Legacy rounds retain their pending order, handled diagnostics and native undo.
Explicit historical answer counters are preserved. Without an explicit answer
counter, ambiguous historical handled positions are not reported as recalls;
only subsequent confirmed answers count. No historical new-answer data is invented.
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

Withdrawal regression: `scripts/review_withdrawal_test.py` covers the incident,
Again, later withdrawals, queue restoration, conservative legacy recovery and
cross/final-round undo using scratch native collections. The companion
`review_withdrawal_ui_test.py` asserts the real 0/119 ring and presented native
review card after reload; it requires the same isolated build/fixture environment
as the existing browser suite. No production API is needed for these checks.
