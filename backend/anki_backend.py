"""Official Anki engine adapter. Collection belongs to exactly one worker thread.

The default app backend is still AnkiConnect. Importing this module does not
import anki or open a collection; selecting pylib explicitly is required.
"""
import asyncio
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class BackendError(RuntimeError):
    pass


class PylibClient:
    def __init__(self, collection_path, *, sync_endpoint=None, sync_username=None,
                 sync_password=None, sync_hkey=None):
        self.path = Path(collection_path).expanduser().resolve()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='anki-owner')
        self._col = None
        self._closed = False
        self._answer_undo = None
        self._lock_file = None
        self._sync_endpoint = sync_endpoint
        self._sync_username = sync_username
        self._sync_password = sync_password
        self._sync_hkey = sync_hkey
        self._auth = None
        self._journal = None
        self._last_sync_error = None
        self._sync_meta = {}
        self._sync_diagnostics = os.getenv("ANKI_SYNC_DIAGNOSTICS") == "1"

    async def call(self, action, params=None, timeout=30):
        if self._closed:
            raise BackendError('Anki engine is closed')
        # Once submitted, cancellation must not bypass the app's round-state
        # update while Rust goes on to commit. Finish and return the result.
        # timeout is intentionally not a wait_for deadline on mutating calls.
        return await self._wait_owner(asyncio.get_running_loop().run_in_executor(
            self._executor, self._dispatch, action, params or {}))

    @staticmethod
    async def _wait_owner(future):
        while True:
            try:
                done, _ = await asyncio.wait([future], timeout=0.1)
                if done:
                    return future.result()
            except asyncio.CancelledError:
                if future.cancelled():
                    raise

    def _open(self):
        if self._col is None:
            if not self.path.is_file():
                raise BackendError('ANKI_COLLECTION_PATH must point to an existing collection.anki2')
            import fcntl
            from anki.collection import Collection
            lock = open(str(self.path) + '.ir4anki.lock', 'a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                raise BackendError('Another ir4anki owner already holds this collection') from None
            try:
                self._col = Collection(str(self.path))
            except Exception:
                lock.close()
                raise BackendError('Cannot open Anki collection; close desktop Anki before selecting pylib') from None
            self._lock_file = lock
            from answer_journal import AnswerJournal
            try:
                self._journal = AnswerJournal(self.path, self._col)
            except Exception:
                self._close()
                raise BackendError('undo journal cannot be opened or collection binding mismatch; manual recovery required') from None
            # Observability never gates offline collection ownership/recovery.
            import json
            status_path = Path(str(self.path)+'.ir4anki-sync.json')
            try:
                status = json.loads(status_path.read_text()) if status_path.exists() else {}
                if isinstance(status, dict) and status.get('binding') == self._journal.binding:
                    self._sync_meta = status
                    self._last_sync_error = status.get('error')
            except (OSError, ValueError):
                self._sync_meta = {}
                self._last_sync_error = 'Cannot read sync status; local collection is available'
        return self._col

    @staticmethod
    def _diagnostic_exception(exc):
        # Native Rust errors encode SQLite codes in their text. Extract numbers
        # only: messages, repr and tracebacks can contain account/server secrets.
        code = getattr(exc, 'sqlite_errorcode', None)
        if not isinstance(code, int) or not 0 <= code <= 0x7fffffff:
            code = None
        try:
            text = str(exc)
        except Exception:
            text = ''
        match = re.search(r'\bextended_code\s*[:=]\s*(\d{1,10})\b', text)
        if match and int(match[1]) <= 0x7fffffff:
            code = int(match[1])
        return {'class': type(exc).__name__, 'sqlite_primary_code': code & 255 if code is not None else None,
                'sqlite_extended_code': code}

    @staticmethod
    def _diagnostic_integrity_error(text):
        # Never forward arbitrary integrity output: SQLite may echo data/schema
        # values. Only these stock-index error grammars can pass through.
        index = r'(?:ix_(?:cards_(?:nid|sched|usn)|notes_(?:csum|usn)|revlog_(?:cid|usn))|idx_(?:cards_odid|decks_name|fields_name_ntid|graves_pending|notes_mid|notetypes_(?:name|usn)|templates_(?:name_ntid|usn)))'
        patterns = (rf'row \d+ missing from index {index}',
                    rf'wrong # of entries in index {index}',
                    rf'non-unique entry in index {index}')
        if isinstance(text, str) and any(re.fullmatch(pattern, text) for pattern in patterns):
            return text
        return 'SQLite integrity diagnostic withheld'

    def _diagnose(self, col, stage, *, exc=None, operation=None):
        if not self._sync_diagnostics:
            return
        # Called on the existing owner only. Observation must never change the
        # result/exception of the operation, even if SQLite/stat/logging fails.
        try:
            record = {'stage': stage, 'path': str(self.path), 'thread': threading.get_ident()}
            try:
                record['inode'] = self.path.stat().st_ino
            except OSError:
                record['inode'] = None
            if operation is not None:
                record['operation'] = operation
            if exc is not None:
                record['exception'] = self._diagnostic_exception(exc)
            try:
                rows = col.db.all('PRAGMA integrity_check')
                errors = [row[0] for row in rows if row != ['ok'] and row != ('ok',)]
                record.update(integrity='errors' if errors else 'ok', error_count=len(errors))
                if errors:
                    record['first_error'] = self._diagnostic_integrity_error(errors[0])
            except Exception as diagnostic_exc:
                record['diagnostic_exception'] = self._diagnostic_exception(diagnostic_exc)
            logging.getLogger('ir4anki.anki_diagnostics').warning('anki diagnostics %s', json.dumps(record))
        except Exception:
            pass

    @staticmethod
    def _fields(note):
        return {key: {'value': note[key], 'order': i} for i, key in enumerate(note.keys())}

    def _card_info(self, cid):
        from anki.errors import NotFoundError
        col = self._open()
        try:
            card = col.get_card(cid)
            # A card can outlive its note in an inconsistent collection.
            # Match the vanished-card wire contract without repairing data.
            note = card.note()
        except NotFoundError:
            return {}
        model = note.note_type()
        rendered = card.render_output()
        intervals = None
        memory = {'stability':None,'difficulty':None,'retention':None}
        return {'next_intervals':intervals, 'memory_state':memory, 'cardId': card.id, 'note': card.nid, 'deckName': col.decks.name(card.did),
                'modelName': model['name'], 'fields': self._fields(note), 'tags': list(note.tags),
                'question': rendered.question_text, 'answer': rendered.answer_text,
                'css': model.get('css', ''), 'type': card.type, 'queue': card.queue,
                'due': card.due, 'interval': card.ivl, 'factor': card.factor,
                'reps': card.reps, 'lapses': card.lapses, 'left': card.left,
                'ord': card.ord, 'kind': 'cloze' if model['type'] == 1 else 'qa'}

    def _note_info(self, nid):
        from anki.errors import NotFoundError
        col = self._open()
        try:
            note = col.get_note(nid)
        except NotFoundError:
            return {}
        return {'noteId': note.id, 'modelName': note.note_type()['name'],
                'fields': self._fields(note), 'tags': list(note.tags),
                'cards': list(col.find_cards(f'nid:{nid}'))}

    def _dispatch(self, action, params):
        col = self._open()
        if action == 'answerCards':
            from anki.errors import NotFoundError
            from anki.scheduler.v3 import CardAnswer
            import uuid
            results = []
            for answer in params['answers']:
                if answer['ease'] not in (1, 2, 3, 4):
                    raise BackendError('ease must be 1-4')
                try:
                    card = col.get_card(answer['cardId'])
                except NotFoundError:
                    results.append(False)
                    continue
                if self._journal.data:
                    if self._journal.data['phase'] not in ('committed','exporting'):
                        raise BackendError('unresolved prepared undo journal; manual recovery required')
                    self._flush_answer(sync=False)
                    card = col.get_card(answer['cardId'])
                token = uuid.uuid4().hex
                # State lookup initializes native rollover/localOffset defaults.
                # Those are configuration, not part of the answer's undo delta.
                states = col._backend.get_scheduling_states(card.id)
                entry = {'phase':'prepared', 'cid':card.id, 'token':token,
                         'nid':card.nid,
                         'note_before':col.db.first('select * from notes where id=?',card.nid),
                         'before':self._journal.snapshot(card.id), 'round_before':params.get('round_before'), 'round_after':params.get('round_after')}
                self._journal.save(entry)
                card.start_timer()
                ratings = [CardAnswer.AGAIN, CardAnswer.HARD, CardAnswer.GOOD, CardAnswer.EASY]
                built = col.sched.build_answer(card=card, states=states, rating=ratings[answer['ease']-1])
                def answer_and_journal():
                    col.sched.answer_card(built)
                    entry.update(phase='committed', after=self._journal.snapshot(card.id))
                    entry['note_after'] = col.db.first('select * from notes where id=?',card.nid)
                    if entry.get('round_after'):
                        entry['round_after']['last']['snap']['native_token'] = token
                    self._journal.save(entry)
                # Native operations nest in DBProxy.transact. Durably store
                # after-state before the outer SQLite commit: a crash before
                # that commit rolls back and recovery sees exactly before;
                # a crash afterwards has both committed card and journal.
                col.db.transact(answer_and_journal)
                self._answer_undo = (card.id, token, col.undo_status().last_step)
                results.append(True)
            return results
        if action == 'engineRoundTransition':
            d = self._journal.data
            if not d or not self._journal.valid() or params['before'] != d.get('round_after'):
                raise BackendError('undo journal business round guard mismatch')
            self._journal.save(dict(d, round_before=params['before'], round_after=params['after']))
            return None
        if action == 'engineRoundRecovery':
            d = self._journal.data
            if d and d['phase']=='undone':
                return {'before':d.get('round_after'), 'after':d.get('round_undo'), 'undo':True}
            if d and (self._journal.valid() or d['phase']=='exporting'):
                return {'before':d.get('round_before'), 'after':d.get('round_after')}
            return None
        if action == 'engineUndoAck':
            if self._journal.data and self._journal.data['phase']=='undone':
                self._journal.save(None)
            return None
        if action == 'engineCommitUndo':
            if self._journal.data:
                if self._journal.data['phase'] not in ('committed', 'exporting'):
                    raise BackendError('unresolved undo journal; cannot end round')
                self._flush_answer(sync=False)
            return None
        if action == 'engineStatus':
            d = self._journal.data
            return {'backend':'pylib', 'sync_deferred':bool(d), 'undo_available':self._journal.valid(),
                    'last_sync_success':self._sync_meta.get('success_at'),
                    'last_collection_sync':self._sync_meta.get('collection_at'),
                    'last_sync_attempt':self._sync_meta.get('attempt_at'),
                    'sync_stage':self._sync_meta.get('stage', 'never'),
                    'local_changes_pending':bool(col.db.scalar(
                        'select 1 from cards where usn=-1 union all select 1 from notes where usn=-1 '
                        'union all select 1 from revlog where usn=-1 union all select 1 from graves where usn=-1 '
                        'union all select 1 from decks where usn=-1 union all select 1 from deck_config where usn=-1 '
                        'union all select 1 from notetypes where usn=-1 union all select 1 from config where usn=-1 limit 1')),
                    'media_changes_possible':True,
                    'last_sync_error':self._last_sync_error,
                    'journal_phase':d['phase'] if d else None,
                    'pending_export':bool(d and d['phase']=='exporting'),
                    'sync_deferred_reason': ('pending_undo' if d['phase']=='committed' else d['phase']) if d else None,
                    'capabilities':{**dict.fromkeys(['undo','stats','backup','export'],True),
                                    'intervals':False,'memory_state':False}}
        if action == 'nativeUndoToken':
            d = self._journal.data
            return d['token'] if self._journal.valid() and d['cid']==params['card'] else None
        if action == 'nativeUndo':
            d = self._journal.data
            if not d or d['cid'] != params['card'] or d['token'] != params.get('token'):
                raise BackendError('undo unavailable: journal scheduling guard mismatch')
            saved = self._answer_undo
            if saved and col.undo_status().last_step == saved[2]:
                if not self._journal.valid():
                    raise BackendError('undo unavailable: journal scheduling guard mismatch')
                d = dict(d, round_undo=params.get('round_undo'), phase='restoring')
                self._journal.save(d)
                col.undo()
                if self._journal.snapshot(d['cid']) != d['before']:
                    raise BackendError('native undo did not match the full journal; recovery required')
                self._journal.save(dict(d, phase='undone') if d.get('round_undo') else None)
            else:
                try: self._journal.restore(round_undo=params.get('round_undo'))
                except RuntimeError as e: raise BackendError(str(e)) from None
            self._answer_undo = None
            return [True]
        if action == 'setSpecificValueOfCard':
            raise BackendError('unsafe legacy snapshot restore is disabled in pylib; use native undo')
        if action == 'engineStats':
            from importlib.metadata import version
            from google.protobuf.json_format import MessageToDict
            graphs = MessageToDict(col._backend.graphs(search='', days=30),
                                   preserving_proto_field_name=True,
                                   always_print_fields_with_no_presence=True)
            return {'source':'anki','html':col.stats().report(),'graphs':graphs,'version':version('anki')}
        if action == 'exportCollection':
            if self._journal.data:
                raise BackendError('export deferred while undo is pending; explicitly commit first')
            try:
                col.export_collection_package(params['path'], include_media=True, legacy=False)
            finally:
                col.reopen()
                self._answer_undo = None
            return True
        if action == 'createBackup':
            folder = Path(params['folder'])
            folder.mkdir(parents=True, exist_ok=True)
            created = col.create_backup(backup_folder=str(folder), force=True, wait_for_completion=True)
            # Fail-closed provenance: a backup captured while an answer is
            # undoable remains local recovery material, never a download.
            # Digest binding prevents an overwritten filename from inheriting
            # an older safe marker, even if the process crashes during backup.
            import hashlib, json
            files = list(folder.glob('*.colpkg'))
            if created and files:
                artifact = max(files, key=lambda p:p.stat().st_mtime_ns)
                with artifact.open('rb') as f:
                    digest = hashlib.file_digest(f, 'sha256').hexdigest()
                marker = artifact.with_name(artifact.name+'.ir4anki-provenance.json')
                marker.write_text(json.dumps({'download_safe':self._journal.data is None,'sha256':digest}))
                marker.chmod(0o600)
            return created
        if action == 'sync':
            if self._journal.data:
                phase = self._journal.data['phase']
                if phase != 'exporting' and not params.get('commit_undo'):
                    return {'deferred':True,'synced':False,'reason':'pending_undo'}
                if phase not in ('committed','exporting'):
                    raise BackendError('unresolved undo journal; manual recovery required')
                self._flush_answer(sync=True)
                return None
            return self._sync()
        if action == 'findCards':
            return list(col.find_cards(params['query']))
        if action == 'cardsMetadata':
            ids = params['cards']
            columns = ('cardId','note','type','queue','due','interval','factor','reps','lapses','left')
            rows = []
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset+500]
                rows.extend(col.db.all('select c.id,c.nid,c.did,c.type,c.queue,c.due,c.ivl,c.factor,c.reps,c.lapses,c.left '
                                       'from cards c join notes n on n.id=c.nid where c.id in ('+
                                       ','.join('?' for _ in batch)+')', *batch))
            decks = {did:col.decks.name(did) for did in {row[2] for row in rows}}
            by_id = {row[0]:dict(zip(columns, [row[0],row[1],*row[3:]]), deckName=decks[row[2]]) for row in rows}
            return [by_id.get(cid,{}) for cid in ids]
        if action == 'cardsInfo':
            return [self._card_info(cid) for cid in params['cards']]
        if action == 'notesInfo':
            return [self._note_info(nid) for nid in params['notes']]
        if action == 'modelFieldNames':
            model = col.models.by_name(params['modelName'])
            if model is None:
                raise BackendError('Note type does not exist')
            return [f['name'] for f in model['flds']]
        if action == 'addNotes':
            ids = []
            for data in params['notes']:
                model = col.models.by_name(data['modelName'])
                if model is None:
                    raise BackendError('Note type does not exist')
                note = col.new_note(model)
                for key, value in data['fields'].items():
                    note[key] = value
                note.tags = data.get('tags', [])
                state = note.fields_check()
                if state == 1 or (state == 2 and not data.get('options', {}).get('allowDuplicate', False)):
                    ids.append(None)
                    continue
                col.add_note(note, col.decks.id(data['deckName']))
                ids.append(note.id)
            return ids
        if action == 'changeDeck':
            self._diagnose(col, 'set_deck:before')
            try:
                col.set_deck(params['cards'], col.decks.id(params['deck']))
            except Exception as exc:
                self._diagnose(col, 'set_deck:failure', exc=exc, operation='set_deck')
                raise
            return None
        if action in ('suspend', 'unsuspend'):
            fn = col.sched.suspend_cards if action == 'suspend' else col.sched.unsuspend_cards
            fn(params['cards'])
            return True
        if action == 'updateNoteFields':
            data = params['note']
            note = col.get_note(data['id'])
            for key, value in data['fields'].items():
                note[key] = value
            col.update_note(note)
            return None
        if action == 'updateNoteTags':
            note = col.get_note(params['note'])
            note.tags = params['tags']
            col.update_note(note)
            return None
        if action == 'getTags':
            return list(col.tags.all())
        if action == 'deleteNotes':
            d = self._journal.data
            if d and (d.get('nid') or col.get_card(d['cid']).nid) in params['notes']:
                if d['phase'] not in ('committed','exporting'):
                    raise BackendError('unresolved undo journal; cannot delete its note')
                # Deletion is an explicit destructive action ending this slot.
                self._flush_answer(sync=False)
            col.remove_notes(params['notes'])
            return None
        if action == 'storeMediaFile':
            import base64
            name = params['filename']
            if not name or '/' in name or '\\' in name or name in ('.', '..'):
                raise BackendError('Invalid media filename')
            return col.media.write_data(name, base64.b64decode(params['data'], validate=True))
        raise BackendError(f'Unsupported Anki action: {action}')

    def _flush_answer(self, *, sync):
        # Persist commit intent before touching the server; explicit failed sync
        # keeps an exporting barrier. The next grade may end that prior slot,
        # retaining local native changes while never undoing exported data.
        if sync:
            self._journal.save(dict(self._journal.data, phase='exporting'))
        self._answer_undo = None
        if sync:
            self._sync()
        self._journal.save(None)

    def _save_sync_status(self, **changes):
        import json, os
        self._sync_meta.update(changes, binding=self._journal.binding)
        target = Path(str(self.path)+'.ir4anki-sync.json')
        temp = target.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(self._sync_meta, f); f.flush(); os.fsync(f.fileno())
        os.replace(temp, target)
        fd = os.open(target.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _sync(self):
        import time
        from datetime import datetime, timezone
        from anki.sync_pb2 import SyncAuth, SyncCollectionResponse
        col = self._open()
        now = lambda: datetime.now(timezone.utc).isoformat(timespec='seconds')
        self._save_sync_status(attempt_at=now(), stage='collection', error=None)
        self._diagnose(col, 'sync:before')
        operation = 'configuration'
        try:
            if not self._sync_endpoint or not (self._sync_hkey or (self._sync_username and self._sync_password)):
                raise BackendError('Anki sync is not configured; explicit endpoint and credentials required')
            if self._auth is None:
                if self._sync_hkey:
                    self._auth = SyncAuth(hkey=self._sync_hkey, endpoint=self._sync_endpoint)
                else:
                    operation = 'sync_login'
                    self._auth = col.sync_login(self._sync_username, self._sync_password, self._sync_endpoint)
            operation = 'sync_collection'
            out = col.sync_collection(self._auth, sync_media=False)
            self._diagnose(col, 'sync:after_collection')
            if out.required not in (SyncCollectionResponse.NO_CHANGES, SyncCollectionResponse.NORMAL_SYNC):
                raise BackendError('Anki requires full sync; resolve upload/download in desktop Anki, never chosen automatically')
            self._save_sync_status(collection_at=now(), stage='media')
            operation = 'sync_media'
            col.sync_media(self._auth)
            deadline = time.monotonic() + 300
            operation = 'media_sync_status'
            while col.media_sync_status().active:
                if time.monotonic() >= deadline:
                    operation = 'abort_media_sync'
                    col.abort_media_sync()
                    raise BackendError('Anki media sync timed out')
                time.sleep(0.05)
            self._diagnose(col, 'sync:after_media')
        except Exception as exc:
            self._diagnose(col, 'sync:failure', exc=exc, operation=operation)
            self._auth = None
            message = str(exc) if isinstance(exc, BackendError) else 'Anki sync failed; verify credentials and server availability'
            self._last_sync_error = message
            self._save_sync_status(stage='failed', failed_stage=self._sync_meta['stage'], error=message)
            raise BackendError(message) from None
        self._last_sync_error = None
        self._save_sync_status(success_at=now(), stage='complete', error=None, failed_stage=None)
        return None

    def _close(self):
        try:
            if self._col is not None:
                self._col.close()
                self._col = None
        finally:
            if self._lock_file is not None:
                self._lock_file.close()
                self._lock_file = None

    async def close(self):
        if not self._closed:
            self._closed = True
            try:
                await self._wait_owner(asyncio.get_running_loop().run_in_executor(self._executor, self._close))
            finally:
                self._executor.shutdown(wait=True)
