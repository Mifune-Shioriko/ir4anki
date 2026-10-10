"""Disposable official Anki fixtures; no profile, plugin or remote credentials."""
import asyncio
import atexit
import os
import tempfile
from pathlib import Path
from anki.collection import Collection

_temps = []
def tempdir(prefix='native-'):
    obj = tempfile.TemporaryDirectory(prefix=prefix, dir=os.environ.get('NATIVE_FIXTURE_ROOT', '/tmp'))
    _temps.append(obj)
    return obj.name

def configure(env=None):
    env = os.environ if env is None else env
    root = Path(tempdir())
    path = root / 'collection.anki2'
    col = Collection(str(path))
    try:
        for source, name, fields in [('Basic', '问答题', ['正面', '背面']), ('Cloze', '填空题', ['文字', '背面额外'])]:
            model = col.models.by_name(source)
            for field, target in zip(list(model['flds']), fields):
                col.models.rename_field(model, field, target)
            model['name'] = name
            col.models.save(model)
        col.set_config('fsrs', True)
        for name in ['2026', '预览池']:
            col.decks.id(name)
    finally:
        col.close()
    for key in list(env):
        if key.startswith('ANKI_SYNC_'):
            env.pop(key)
    env.update(ANKI_BACKEND='pylib', ANKI_COLLECTION_PATH=str(path),
               ANKI_MEDIA_DIR=str(path.with_suffix('.media')), ANKI_BACKUP_DIR=str(root/'backups'),
               ANKI_SYNC_ENDPOINT='', ANKI_SYNC_USERNAME='', ANKI_SYNC_PASSWORD='', ANKI_SYNC_HKEY='')
    for key, leaf in [('ANKI_STATE_DIR', 'state'), ('ANKI_NOTES_DIR', 'corpus')]:
        value = Path(env.get(key, '/')).resolve()
        if not any(value.is_relative_to(Path(obj.name)) for obj in _temps):
            env[key] = str(root/leaf)
    env.update(ANKI_ADD_MODEL='问答题', ANKI_ADD_CLOZE_MODEL='填空题')
    Path(env['ANKI_NOTES_DIR']).mkdir(parents=True, exist_ok=True)
    print(f'fixture backend=pylib native=anki.Collection FSRS=true collection={path}', flush=True)
    return path

async def run_closed(backend, coroutine):
    async def heartbeat():
        # Restricted environments can suppress asyncio worker self-pipe wakeups.
        # A timer keeps the loop responsive without replacing engine behavior.
        while True:
            await asyncio.sleep(.01)
    ticker = asyncio.create_task(heartbeat())
    try:
        assert backend._pylib is not None
        await backend.anki('findCards', {'query': ''})
        return await coroutine
    finally:
        if backend._sync_timer is not None:
            backend._sync_timer.cancel()
        await backend._pylib.close()
        ticker.cancel()
        cleanup()

class NativeData:
    """Seed/read native records on the adapter's owner thread. Never dispatch fake actions."""
    def __init__(self, backend):
        self.backend = backend
    def native(self, fn):
        owner = self.backend._pylib
        return owner._executor.submit(lambda: fn(owner._open())).result()
    def clear(self):
        self.native(lambda col: col.remove_notes(col.find_notes('')))
    def add_card(self, cid, *, deck, note, ctype=0, suspended=False, tags=None):
        def seed(col):
            n = col.new_note(col.models.by_name('问答题'))
            n['正面'], n['背面'] = f'Q{cid}', f'A{cid}'
            n.tags = list(tags or [])
            col.add_note(n, col.decks.id(deck))
            c = n.cards()[0]
            # Native setup before any scheduler answer; historical ids encode Anki days.
            col.db.execute('update notes set id=? where id=?', note, n.id)
            col.db.execute('update cards set nid=?, id=? where id=?', note, cid, c.id)
            c = col.get_card(cid)
            if ctype:
                c.type = 2; c.queue = 2; c.ivl = 1; c.reps = 1; c.factor = 2500
                c.due = col.sched.today
                col.update_card(c)
            if suspended:
                col.sched.suspend_cards([cid])
        self.native(seed)
    def add_pool_card(self, nid, *, deck='预览池', suspended=True, ctype=0):
        cid = max([900000, *self.cards]) + 1
        self.add_card(cid, deck=deck, note=nid, suspended=suspended, ctype=ctype)
        return cid
    @property
    def cards(self):
        def read(col):
            result = {}
            for cid in col.find_cards(''):
                c = col.get_card(cid)
                result[cid] = dict(cardId=cid, note=c.nid, queue=c.queue, type=c.type,
                    deckName=col.decks.name(c.did), deck=col.decks.name(c.did), susp=c.queue == -1)
            return result
        return self.native(read)
    def age_note(self, old, new):
        def age(col):
            col.db.execute('update notes set id=? where id=?', new, old)
            col.db.execute('update cards set nid=? where nid=?', new, old)
        self.native(age)

def cleanup():
    while _temps:
        _temps.pop().cleanup()

atexit.register(cleanup)

def cleanup_after(fn):
    import functools, signal
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        def terminate(signum, frame):
            raise SystemExit(128+signum)
        previous = signal.signal(signal.SIGTERM, terminate)
        try:
            return fn(*args, **kwargs)
        finally:
            signal.signal(signal.SIGTERM, previous)
            cleanup()
    return wrapped

def reserve_loopback_port():
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def verify_server(proc, base):
    import json, time, urllib.request
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError(f'disposable server exited: {proc.returncode}')
        try:
            with urllib.request.urlopen(base+'/api/status', timeout=.5) as response:
                status = json.load(response)
            assert status['anki_backend'] == 'pylib', status
            assert status['anki'] == 'ok', status
            print('server identity verified: backend=pylib native collection healthy', flush=True)
            return
        except OSError:
            time.sleep(.05)
    raise RuntimeError('disposable native server did not become ready')
