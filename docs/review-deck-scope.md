# Recall deck scope

`ANKI_REVIEW_DECK` defaults to blank (all decks, existing behavior). Setting
`ANKI_REVIEW_DECK=2026` selects 2026 and every descendant for due review and
dealable new cards. Spaces, quotes, backslashes and wildcard characters in deck
names are escaped for Anki search. This is a backend setting, with no UI selector.
Existing suspension exclusion is preserved.

The scope applies to batch draws, status/session counts, study-mode new totals,
and daily due snapshots. Snapshots are keyed by deck scope as well as Anki day.
Session and whole-round flow resume reconcile pending cards against the current
scope, including cards moved to another deck after dealing. Answers recheck the
pending queue before scheduling. Undo can restore an earlier out-of-scope answer,
but does not return that card for recall; its pending entry is reconciled away.

Authoring destination, preview pool release/deck/cap, reading scheduling and
provenance-related card displays/counts are unchanged. Related cards are context,
not a review draw. No cards are moved, deleted, suspended or rescheduled by the
filter itself.

## Activation and reversal

Obtain restart/undo approval separately. Before changing scope, finish or
explicitly exit the existing whole round through the normal app controls, after
resolving any undo slot. Back up the state directory and native recovery material
using the established procedure. Do not delete JSON queues or engine journals.
Then set the machine-local environment value and restart the approved service.
This change does not edit that configuration or perform a restart, sync or deploy.

Old persisted queues are supported without a schema migration: normal resume
removes out-of-scope pending IDs durably, using the existing native round journal
transition when undo is available. These removals count as handled slots, not
answers; original batch totals/composition remain historical. The flow reconciles
those slots without changing reading scheduling. Undo history is retained.
Production state has not been inspected or changed by this task.

To reverse, set `ANKI_REVIEW_DECK=` and obtain the corresponding restart approval.
All decks become eligible again; IDs already reconciled out of an old queue are
available in a fresh draw, not reinserted into that historical round. Exiting the
old round before switching avoids this queue caveat.
