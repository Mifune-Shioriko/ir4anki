# Native Anki engine migration

The native adapter uses official `anki==25.2.7`, with all Collection calls
(including open, sync, backup and close) on one dedicated owner thread.
`ANKI_BACKEND=connect` remains the default and rollback path. No live service
has been switched by this change. New QA/cloze cards preserve raw Markdown,
CM6 editing is shared, and native statistics/backup/export management is available.

## Tested boundaries

- P0: consistent SQLite backup of the x13 collection, then mutations ONLY in
  that copy. QA/cloze creation, duplicate preflight, rendering, deck moves,
  suspend/unsuspend, fields/tags/delete, scheduler answer + exact native undo
  (cards and revlog), native colpkg backup.
- P0 synchronization: independent localhost server with temporary accounts,
  full upload, incremental update, second client download, media round trip.
  No experimental copy was connected to the production account.
- New adapter tests use genuine temporary Anki collections, not a fake plugin.
  Existing fake-protocol tests remain necessary for the connect rollback path.

## Before switching x13

1. Finish/exit the active ir4anki round. Sync desktop Anki, then quit it fully.
   Keep desktop and pylib versions aligned. This release pins 25.02.7 / 25.2.7.
2. Stop the ir4anki service. Preserve a consistent collection backup, its media,
   ir4anki state (including reading.db and JSON ledgers), corpus and old env.
   Do not copy only a SQLite main file while it has active WAL writers.
3. Install the pinned backend dependencies. Do not run update.sh merely to
   apply local uncommitted changes; it pulls and restarts services.
4. Edit ~/.config/ir4anki.env locally. The agent does not write your secrets:

```ini
ANKI_BACKEND=pylib
ANKI_COLLECTION_PATH=/home/shioriko/.local/share/Anki2/shioriko/collection.anki2
ANKI_MEDIA_DIR=/home/shioriko/.local/share/Anki2/shioriko/collection.media
ANKI_BACKUP_DIR=/home/shioriko/.local/state/ir4anki/anki-backups
ANKI_BACKUP_INTERVAL=1800
ANKI_SYNC_ENDPOINT=<your actual Beelink sync URL, including trailing slash>
ANKI_SYNC_USERNAME=<your sync account>
ANKI_SYNC_PASSWORD=<your sync password>
```

Alternatively use ANKI_SYNC_HKEY instead of username/password. Endpoint is
mandatory; there is no implicit AnkiWeb fallback. Use absolute paths, not ~.
Keep existing state/corpus/pacing variables unchanged. Restrict env permissions
with `chmod 600 ~/.config/ir4anki.env`.

5. Start ir4anki with ONE worker, then check /api/status contains
   `anki_backend: pylib` and `anki: ok`. Verify normal sync succeeds, media works,
   backups exist, and a fresh round matches the desktop's previously synced data.

```sh
cd /home/shioriko/ir4anki
uv pip install --python backend/.venv/bin/python --index-url https://pypi.org/simple -r backend/requirements.txt
systemctl --user start ir4anki
journalctl --user -u ir4anki -n 40 --no-pager
```

The app refuses missing collection paths rather than creating a blank database.
It holds an advisory owner lock; the native Anki engine also detects a collection
already opened by Anki. These are not a license to run concurrent desktop writers.

## Synchronization and undo constraints

Normal collection sync and media sync are implemented. A full-sync-required
response is a hard stop, never an automatic upload/download decision. Stop
ir4anki, open the matching desktop client, explicitly resolve the full sync,
close desktop, then restart ir4anki. Missing credentials fail visibly in logs
and through the existing `synced: false` response; local use is still possible.

Pylib keeps a durable single-answer journal beside the collection
(`collection.anki2.ir4anki-answer.json`). It binds to collection path, inode,
creation time and engine version. Native undo is preferred; after restart,
guarded restoration includes the complete card row (FSRS data), new revlog
entries and changed deck counters. Mismatched snapshots fail closed.

Background sync is deferred while the latest answer remains undoable; there is
no timed undo window. Grading the next card ends the previous slot, attempts
sync, then creates a new undo slot. A failed prior sync does not block local
grading: local native changes remain pending, while the old undo authority is
discarded because a failed network response may already have exported data.
Explicit sync with `commit_undo=true` also ends undo and requires UI confirmation.
Thus restart/reload/background sync requests preserve undo, but actual export of
the answer to the server does not. This is not cross-sync multi-level undo.

Keep the journal with the collection during backup/rollback. Do not delete it
to bypass recovery guards. Desktop emergency use must first resolve or explicitly
commit the pending slot; never run both owners concurrently. The connect backend
retains its historical snapshot behavior.

Engine management endpoints require `ANKI_ENGINE_API_TOKEN` as a bearer token
(except non-sensitive engine status). Set it locally and enter it in the engine
panel; it is held only in page memory. Downloads use authenticated fetch, not
tokens in URLs. Empty token configuration disables protected operations.

Backups are created before serving pylib requests and every 30 minutes (interval
configurable). The native backup contains collection data, not a substitute for
media/corpus/reading-state backups. Anki's configured backup retention applies.
Backups made with a pending answer are local recovery material only. Downloads
require an artifact-specific safe marker bound to its SHA-256, not merely an
empty current undo slot. Missing, changed or pending-answer provenance is refused.
Do not remove these markers to bypass download checks.

Answers run inside an outer `DBProxy.transact`: the native scheduler writes and
fsynced after-state journal precede the outer SQLite commit. Actual process-crash
tests cover before journal save, after journal save/before SQLite commit and after
SQLite commit/before acknowledgement. Recovery either sees the unchanged before
state and clears the journal or sees a committed answer with durable undo.
Foreign/pre-existing inconsistent journals remain fail-closed.

## Emergency desktop and rollback

- `systemctl --user stop ir4anki`, then open desktop Anki.
- Close desktop completely before starting pylib again.
- To revert to connect: stop ir4anki, edit ANKI_BACKEND=connect, open desktop
  Anki with AnkiConnect, and start ir4anki. Do not open both concurrently.
- Backend choice alone cannot undo schema upgrades, bad sync decisions, or
  earlier writes; those require the saved backups.

## Reproducible tests

```sh
uv venv /tmp/ir4anki-test
uv pip install --python /tmp/ir4anki-test/bin/python --index-url https://pypi.org/simple -r backend/requirements.txt pytest playwright
/tmp/ir4anki-test/bin/python -m pytest scripts/pylib_backend_test.py scripts/pylib_sync_test.py scripts/pylib_app_test.py -q
```

All test mutations use disposable collections. The app integration test suppresses
background sync for deterministic undo checks; synchronization is separately
exercised against the real local sync server. It is not a production-account sync
verification. Behavioral/UI regression scripts now use disposable real pylib
fixtures; targeted connect protocol/fault coverage remains in sync_throttle.
See `scripts/REGRESSION.md` for the complete runner and isolation guarantees.

## Operator tools and deployment boundary

New installs default to pylib; the app's fallback stays connect and existing env
files are not overwritten. Use `deploy/install.sh --help` for explicit backend,
collection and no-service options. `deploy/update.sh --local --no-restart` prepares
an isolated build without replacing the live dist or restarting a service.
Default updates refuse a dirty working tree before pulling.

`deploy/native.py check --env /absolute/config --require-offline` validates pinned
engine, collection/model/media configuration and desktop shutdown without opening
the source through pylib. `backup --env /absolute/config --output /absolute/backup
--offline-confirmed` additionally requires a stopped user service, snapshots the
SQLite collection consistently, and copies media, state, corpus and a private env
file. The operator must resolve or explicitly commit any pending answer journal first;
offline snapshots refuse it rather than silently losing restart undo.
The operator must keep this directory private: environment.private contains
credentials. Symlinks are preserved, not recursively materialized; separately
back up external symlink targets. Snapshotting and backend selection do not resolve
full-sync conflicts automatically. The installed service must use one worker and
a sufficient graceful shutdown budget (the repository template uses 300 seconds).

No agent-run test constitutes production migration, production sync/media
verification or completion of an observation period.

## Report corrections from actual 25.2.7 execution

- `find_cards()` returns a protobuf repeated container: convert it to list.
- Scheduling states in this version come from `col._backend.get_scheduling_states`.
- `add_note()` does not enforce AnkiConnect's duplicate policy: call fields_check.
- Backup directory must exist; create_backup has keyword-only options.
- Sync response is SyncCollectionResponse; it has no server_usn attribute.
- Media synchronization is explicitly started and polled before returning.
- Actual app uses two additional actions: media upload and legacy snapshot undo.
- AGPL adoption does not revoke past MIT grants; original notice is in NOTICE.
