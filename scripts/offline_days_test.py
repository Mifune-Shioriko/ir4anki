"""Five accelerated native calendar days, real process restarts, no remote sockets.
Requires FIXTURE_CLOCK_LIBRARY: scratch-built fake-time.so interposes REALTIME only.
Never changes host clock, firewall, collection or service.
"""
import os,sys,asyncio,json,subprocess,socket
from pathlib import Path
from native_fixture import configure,NativeData,run_closed
ROOT=Path(__file__).resolve().parents[1]

def child(day, resume):
    sys.path.insert(0,str(ROOT/'backend'))
    real_socket=socket.socket
    def guarded(family=socket.AF_INET,*a,**kw):
        if family in (socket.AF_INET,socket.AF_INET6):raise AssertionError('remote socket forbidden during offline work')
        return real_socket(family,*a,**kw)
    socket.socket=guarded
    import app as b
    async def run():
        native=NativeData(b)
        attempts=[]
        def forbidden(*a,**k):
            attempts.append('sync');raise AssertionError('implicit native sync')
        b._pylib._sync=forbidden
        col=b._pylib._col
        today=native.native(lambda c:c.sched.today)
        if resume:
            d=await b.flow_state()
            assert d['can_undo'], 'restart lost final undo'
            await b.undo()
            d=await b.flow_state();cid=d['cards'][0]['cardId']
            assert (await b.answer(cid,3))['answered']
        else:
            if day:
                status=await b.engine_status()
                if not status['undo_available']:
                    def diff(c):
                        j=b._pylib._journal;d=j.data
                        if not d:return {'data':None}
                        current=j.snapshot(d['cid'])
                        return {k:[d['after'][k],current[k]] for k in current if d['after'][k]!=current[k]}
                    print('UNDO DIFF',json.dumps(native.native(diff),default=str),flush=True)
                assert status['undo_available'], 'overnight restart lost native undo'
            for i in range(3):native.add_card(1000+day*10+i,deck='2026',note=10000+day*10+i)
            path=Path(os.environ['ANKI_NOTES_DIR'])/'reading.md'
            if not path.exists():
                b.NOTES_ASSETS_DIR.mkdir(parents=True,exist_ok=True)
                import base64
                (b.NOTES_ASSETS_DIR/'test.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF1sAAAAASUVORK5CYII='))
                path.write_text('# Offline\n\nLocal passage $x^2$ with image ![](test.png).\n')
                await b.reading_list_add(b.ReadingPathBody(path='reading.md'))
            d=await b.flow_start();seq=[]
            while d['flow']['phase']!='complete':
                if d['flow']['phase']=='reading':
                    seq.append('R');ch=d['reading_round']['chunks'][0]
                    await b.reading_act(ch['path'],ch['chunk_key'],'next')
                    d=await b.flow_state()
                else:
                    seq.append('C');cid=d['cards'][0]['cardId'];r=await b.answer(cid,3)
                    assert r['answered'];d=r['state']
                    assert not (await b.answer(cid,3))['answered']
            assert d['can_undo']
            # Ordinary authoring and legacy finish remain fully local.
            cid=1000+day*10
            note=await b.get_note(cid)
            await b.update_note(b.NoteUpdate(card_id=cid,fields=note['fields'],tags=note['tags']))
            await b.flow_exit()
            # exit deliberately ends undo; create a fresh final answer to test next-day reload
            native.add_card(2000+day,deck='2026',note=20000+day)
            d=await b.flow_start()
            if d['flow']['phase']=='reading':await b.flow_end_reading();d=await b.flow_state()
            while d['flow']['phase']=='review':
                d=(await b.answer(d['cards'][0]['cardId'],3))['state']
            assert d['can_undo']
            print(json.dumps({'day':day,'native_today':today,'sequence':seq,'revlog':native.native(lambda c:c.db.scalar('select count(*) from revlog')),'network_attempts':attempts}),flush=True)
        assert not attempts
    async def started():
        async with b.app.router.lifespan_context(b.app):
            await run()
    asyncio.run(run_closed(b,started()))

if __name__=='__main__':
    if len(sys.argv)>1:child(int(sys.argv[1]),len(sys.argv)>2)
    else:
        for k in list(os.environ):
            if k.startswith('ANKI_'):os.environ.pop(k)
        os.environ.update(ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='1')
        configure()
        os.environ.update(ANKI_SYNC_ENDPOINT='http://127.0.0.1:1/',ANKI_SYNC_HKEY='fixture-only',ANKI_SYNC_POLICY='manual')
        lib=Path(os.environ['FIXTURE_CLOCK_LIBRARY']).resolve();assert lib.is_file()
        days=[]
        for day in range(5):
            env=dict(os.environ,LD_PRELOAD=str(lib),FIXTURE_DAY_OFFSET=str(day))
            p=subprocess.run([sys.executable,__file__,str(day)],env=env,text=True,capture_output=True,timeout=60)
            print(p.stdout,end='');sys.stderr.write(p.stderr);assert p.returncode==0
            record=json.loads(p.stdout.strip().splitlines()[-1]);days.append(record['native_today'])
            p=subprocess.run([sys.executable,__file__,str(day),'resume'],env=env,text=True,capture_output=True,timeout=60)
            sys.stderr.write(p.stderr);assert p.returncode==0,p.stdout
        assert days==list(range(days[0],days[0]+5)),days
        print('PASS five accelerated native days + ten independent process starts (not five real elapsed days)')
