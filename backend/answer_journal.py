"""Durable single-answer journal. Never permits export while unresolved."""
import json
import os
from importlib.metadata import version
from pathlib import Path

class AnswerJournal:
    def __init__(self, path, col):
        self.path = Path(str(path)+'.ir4anki-answer.json')
        self.col = col
        self.binding = [str(path), col.db.scalar('select crt from col'), Path(path).stat().st_ino, version('anki')]
        self.data = json.loads(self.path.read_text()) if self.path.exists() else None
        if self.data and self.data['binding'] != self.binding:
            raise RuntimeError('undo journal collection binding mismatch')
        if self.data:
            # Authoring counters are unrelated to scheduling. Older journals
            # included them; normalize both snapshots without relaxing any
            # card, revlog, deck or scheduling configuration guards.
            for key in ('before', 'after'):
                if self.data.get(key):
                    self.data[key]['config'] = self.scheduling_config(self.data[key]['config'])
            self.save(self.data)
            current = self.snapshot(self.data['cid'])
            if current == self.data['before']:
                if self.data.get('round_undo'):
                    self.save(dict(self.data, phase='undone'))
                else:
                    self.save(None)
            elif self.data['phase'] == 'restoring' and current == self.data['after']:
                self.restore()


    def save(self, data):
        if data is not None:
            data['binding'] = self.binding
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f); f.flush(); os.fsync(f.fileno())
        os.replace(temp, self.path)
        fd=os.open(self.path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        self.data=data

    @staticmethod
    def scheduling_config(rows):
        return [r for r in rows if r[0] not in ('nextPos', 'curModel')
                and not (r[0].startswith('_deck_') and r[0].endswith('_lastNotetype'))
                and not (r[0].startswith('_nt_') and r[0].endswith('_lastDeck'))]

    def snapshot(self, cid):
        return {'card': self.col.db.first('select * from cards where id=?',cid),
                'revlog': self.col.db.all('select * from revlog where cid=? order by id',cid),
                'decks': self.col.db.all('select * from decks order by id'),
                'deck_config':self.col.db.all('select * from deck_config order by id'),
                'config':self.scheduling_config(self.col.db.all('select * from config order by key'))}

    def valid(self):
        d=self.data
        return bool(d and d['phase']=='committed' and self.snapshot(d['cid'])==d['after'])

    def restore(self):
        d=self.data
        if d['phase'] not in ('committed','restoring') or self.snapshot(d['cid']) != d['after']:
            raise RuntimeError('undo journal scheduling guard mismatch or incomplete answer')
        # Entire card row, including FSRS data and original-deck/due state;
        # only this unexported answer's revlog and scheduler deck counters.
        self.save(dict(d, phase='restoring'))
        db=self.col.db
        db.execute('begin immediate')
        try:
            columns=[r[1] for r in db.all('pragma table_info(cards)')]
            db.execute('update cards set '+','.join(f'{c}=?' for c in columns)+' where id=?',*d['before']['card'],d['cid'])
            old_ids = {r[0] for r in d['before']['revlog']}
            for row in d['after']['revlog']:
                if row[0] not in old_ids:
                    db.execute('delete from revlog where id=? and cid=?',row[0],d['cid'])
            after_decks = {row[0]:row for row in d['after']['decks']}
            for row in d['before']['decks']:
                if after_decks.get(row[0]) == row:
                    continue
                db.execute('update decks set name=?,mtime_secs=?,usn=?,common=?,kind=? where id=?',*row[1:],row[0])
            # Scheduler note changes (e.g. leech) are separate from the
            # scheduling guard so legitimate later field/tag edits survive.
            nb, na = d.get('note_before'), d.get('note_after')
            if nb is not None and na is not None and nb != na:
                current = db.first('select * from notes where id=?',d['nid'])
                if current == na:
                    columns = [r[1] for r in db.all('pragma table_info(notes)')]
                    db.execute('update notes set '+','.join(f'{c}=?' for c in columns)+' where id=?',*nb,d['nid'])
                elif current is not None:
                    columns = [r[1] for r in db.all('pragma table_info(notes)')]
                    i = columns.index('tags')
                    before_tags, after_tags = set(nb[i].split()), set(na[i].split())
                    tags = set(current[i].split())
                    tags.difference_update(after_tags-before_tags)
                    tags.update(before_tags-after_tags)
                    import time
                    tagstr = ' '+' '.join(sorted(tags))+' ' if tags else ''
                    db.execute('update notes set tags=?,mod=?,usn=-1 where id=?',tagstr,int(time.time()),d['nid'])
            db.execute('commit')
        except BaseException:
            db.execute('rollback');raise
        self.col.sched.reset()
        self.save(dict(d, phase='undone') if d.get('round_undo') else None)
