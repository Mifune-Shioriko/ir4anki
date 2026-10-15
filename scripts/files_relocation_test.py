"""Disposable corpus/SQLite/flow/collection. No HTTP socket or real service."""
import os, sys, json, subprocess, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
if '--recover' in sys.argv:
    import app
    app._files_relocator().recover()
    raise SystemExit(0)
# Explicitly remove inherited storage overrides before importing backend.
for key in list(os.environ):
    if key.startswith('ANKI_'):
        os.environ.pop(key)
from native_fixture import configure, tempdir, cleanup
os.environ['ANKI_STATE_DIR'] = tempdir('files-journal-state-')
os.environ['ANKI_NOTES_DIR'] = tempdir('files-journal-corpus-')
os.environ['ANKI_READING_MODE'] = '1'
configure()
import app
from file_relocation import RelocationConflict

class Crash(BaseException): pass
passed = []
def check(label, fn):
    fn(); passed.append(label); print('PASS', label, flush=True)
def seed(name):
    path = app.NOTES_DIR / name
    path.mkdir()
    (path / '文.md').write_text('# 合成\n\n正文 $x^2$\n')
    conn = app._reading_conn()
    with conn:
        conn.execute('INSERT INTO rfiles(path,in_list,file_sha) VALUES(?,1,?)',(name+'/文.md','stable-sha'))
        conn.execute("INSERT INTO segments(path,seg_id,start_line,end_line,fingerprint,status) VALUES(?,1,1,3,'fingerprint','done')",(name+'/文.md',))
        conn.execute('INSERT INTO rcards(path,seg_id,note_id) VALUES(?,1,42)',(name+'/文.md',))
    conn.close()
    app._flow_write(dict(status='active',policy='even-floor-v1',slots=[[[name+'/文.md','1']]],card_ids=[]))

def crash_case(phase):
    old='源-'+phase; new='目标-'+phase; seed(old)
    relocator = app._files_relocator()
    relocator.fault = lambda p: (_ for _ in ()).throw(Crash()) if p == phase else None
    try: relocator.move(old,new,True)
    except Crash: pass
    else: raise AssertionError('fault did not fire')
    # Real new process; no live startup/engine request and all paths disposable.
    result = subprocess.run([sys.executable,__file__,'--recover'],capture_output=True,text=True,env=os.environ)
    assert result.returncode == 0, result.stderr
    assert not (app.NOTES_DIR/old).exists() and (app.NOTES_DIR/new/'文.md').exists()
    conn=app._reading_conn()
    assert conn.execute('SELECT file_sha FROM rfiles WHERE path=?',(new+'/文.md',)).fetchone()[0]=='stable-sha'
    assert conn.execute('SELECT status,fingerprint FROM segments WHERE path=?',(new+'/文.md',)).fetchone()==('done','fingerprint')
    assert conn.execute('SELECT note_id FROM rcards WHERE path=?',(new+'/文.md',)).fetchone()[0]==42
    conn.close()
    assert [new+'/文.md','1'] in app._flow_read()['slots'][0]
    app._files_relocator().recover()  # idempotent

try:
    for phase in ['fs_before','fs','db_before','db','db_durable','flow_before','flow']:
        check('restart recovery '+phase, lambda phase=phase: crash_case(phase))
    seed('撤销源')
    result=app._files_relocator().move('撤销源','撤销目标',True)
    undone=app._files_relocator().undo(result['operationId'])
    assert undone['path']=='撤销源'
    assert app._files_relocator().undo(result['operationId'])['path']=='撤销源'
    passed.append('undo and repeat dedup')
    result=app._files_relocator().move('撤销源','撤销目标2',True)
    (app.NOTES_DIR/'撤销源').mkdir()
    try: app._files_relocator().undo(result['operationId'])
    except RelocationConflict: pass
    else: raise AssertionError('undo overwrote conflict')
    passed.append('undo no-overwrite conflict')
    (app.NOTES_DIR/'撤销目标2/文.md').write_text('changed')
    try: app._files_relocator().undo(result['operationId'])
    except RelocationConflict: pass
    else: raise AssertionError('undo ignored version')
    passed.append('undo rejects changed content')
    seed('DB冲突源')
    conn=app._reading_conn()
    with conn: conn.execute('INSERT INTO rfiles(path) VALUES(?)',('DB冲突目标/文.md',))
    conn.close()
    try: app._files_relocator().move('DB冲突源','DB冲突目标',True)
    except RelocationConflict: pass
    else: raise AssertionError('DB preflight missed conflict')
    assert (app.NOTES_DIR/'DB冲突源/文.md').exists()
    passed.append('DB conflict before FS')
    # DB expected-state guard catches subsequent reading progress changes.
    seed('进度撤销源')
    result=app._files_relocator().move('进度撤销源','进度撤销目标',True)
    conn=app._reading_conn()
    with conn: conn.execute("UPDATE segments SET status='todo' WHERE path=?",('进度撤销目标/文.md',))
    conn.close()
    try: app._files_relocator().undo(result['operationId'])
    except RelocationConflict: pass
    else: raise AssertionError('undo ignored changed progress')
    passed.append('undo expected reading state')
    # Recovery conflict does not overwrite a new object at the old location.
    seed('恢复冲突源')
    relocator=app._files_relocator()
    relocator.fault=lambda phase: (_ for _ in ()).throw(Crash()) if phase=='fs' else None
    try: relocator.move('恢复冲突源','恢复冲突目标',True)
    except Crash: pass
    (app.NOTES_DIR/'恢复冲突源').mkdir()
    unrelated=app.NOTES_DIR/'恢复冲突源/新.md'
    unrelated.write_text('unrelated')
    try: app._files_relocator().recover()
    except RelocationConflict: pass
    else: raise AssertionError('recovery ignored conflict')
    assert unrelated.read_text()=='unrelated'
    # Only disposable fixture conflict is removed to allow later assertions.
    unrelated.unlink(); (app.NOTES_DIR/'恢复冲突源').rmdir(); app._files_relocator().recover()
    passed.append('recovery conflict preserves unrelated data')
    seed('符号源')
    (app.NOTES_DIR/'链接').symlink_to(app.NOTES_DIR/'符号源',target_is_directory=True)
    try: app._files_resolve('链接/文.md')
    except app.HTTPException as e: assert e.status_code==400
    else: raise AssertionError('symlink source accepted')
    passed.append('symlink boundaries')
    import errno, ctypes
    from types import SimpleNamespace
    from unittest.mock import patch
    seed('跨设备源')
    def exdev(*args):
        ctypes.set_errno(errno.EXDEV)
        return -1
    with patch('file_relocation.ctypes.CDLL', return_value=SimpleNamespace(renameat2=exdev)):
        try: app._files_relocator().move('跨设备源','跨设备目标',True)
        except RelocationConflict: pass
        else: raise AssertionError('EXDEV accepted')
    assert (app.NOTES_DIR/'跨设备源/文.md').exists() and not (app.NOTES_DIR/'跨设备目标').exists()
    passed.append('EXDEV rejected without copy/delete fallback')
    seed('损坏flow源')
    original_flow=app.FLOW_FILE.read_bytes()
    app.FLOW_FILE.write_text('{')
    try: app._files_relocator().move('损坏flow源','损坏flow目标',True)
    except app.HTTPException as e: assert e.status_code==409
    else: raise AssertionError('corrupt flow accepted')
    assert (app.NOTES_DIR/'损坏flow源/文.md').exists()
    app.FLOW_FILE.write_bytes(original_flow)
    passed.append('corrupt flow preflight before FS')
    before={p:p.read_bytes() for p in Path(os.environ['ANKI_STATE_DIR']).rglob('*') if p.is_file()}
    members=asyncio.run(app.files_membership())
    assert 'DB冲突源/文.md' in members['paths']
    after={p:p.read_bytes() for p in Path(os.environ['ANKI_STATE_DIR']).rglob('*') if p.is_file()}
    assert {p:b for p,b in before.items() if not p.name.endswith(('-wal','-shm'))} == {p:b for p,b in after.items() if not p.name.endswith(('-wal','-shm'))}

    passed.append('membership database/JSON unchanged (SQLite read locks may create sidecars)')
    print(json.dumps({'passed':len(passed),'cases':passed},ensure_ascii=False))
finally:
    cleanup()
