"""Snapshot source read-only; mutate only temp copy and localhost test account."""
import argparse
import os, pathlib, sqlite3, tempfile, json, subprocess, socket, time, urllib.request, traceback
from anki.collection import Collection
from anki.scheduler.v3 import CardAnswer
ROOT=pathlib.Path(tempfile.mkdtemp(prefix='ir4anki-p0-'))
results=[]
def check(name, cond, detail=None):
 results.append({'name':name,'passed':bool(cond),'detail':detail}); print(name, bool(cond), detail or '',flush=True)
 if not cond: raise AssertionError(name)
def snapshot(c):
 return (c.db.all('select * from cards order by id'),c.db.all('select * from revlog order by id'))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=pathlib.Path,required=True)
SRC=parser.parse_args().source.expanduser().resolve()
if not SRC.is_file():parser.error('--source must be an existing collection')
source=sqlite3.connect(SRC.as_uri()+'?mode=ro',uri=True)
dst=sqlite3.connect(ROOT/'collection.anki2'); source.backup(dst); dst.close(); source.close()
col=Collection(str(ROOT/'collection.anki2'))
proc=None
try:
 for q in ['is:due -is:new','is:new -is:suspended','deck:"预览池" is:new is:suspended']:
  ids=list(col.find_cards(q)); check('query '+q, all(isinstance(i,int) for i in ids),len(ids))
 cid=col.find_cards('')[0]; card=col.get_card(cid); r=card.render_output(); note=col.get_note(card.nid)
 check('card rendering/notes',bool(r.question_text and note.fields))
 before=snapshot(col); card.start_timer(); states=col._backend.get_scheduling_states(cid)
 ans=col.sched.build_answer(card=card,states=states,rating=CardAnswer.GOOD); col.sched.answer_card(ans); col.undo()
 check('answer native undo exact cards+revlog',snapshot(col)==before)
 for modelname,fields in [('问答题',{'正面':'P0 unique QA','背面':'P0 answer'}),('填空题',{'文字':'P0 {{c1::unique}} cloze','背面额外':'extra'})]:
  model=col.models.by_name(modelname); check('model '+modelname,bool(model))
  n=col.new_note(model)
  for k,v in fields.items(): n[k]=v
  col.add_note(n,col.decks.id('P0-isolated'))
  dup=col.new_note(model)
  for k,v in fields.items(): dup[k]=v
  check('duplicate preflight '+modelname,dup.fields_check()!=0,int(dup.fields_check()))
  cids=col.find_cards(f'nid:{n.id}'); check('add '+modelname,bool(cids))
  col.sched.suspend_cards(cids); check('suspend',col.get_card(cids[0]).queue==-1)
  col.sched.unsuspend_cards(cids); check('unsuspend',col.get_card(cids[0]).queue!=-1)
  col.set_deck(cids,col.decks.id('P0-target')); check('deck move',col.decks.name(col.get_card(cids[0]).did)=='P0-target')
  n.tags=['p0']; n[n.keys()[-1]]='updated'; col.update_note(n); check('fields tags',col.get_note(n.id).tags==['p0'])
  col.remove_notes([n.id]); check('remove',not col.find_cards(f'nid:{n.id}'))
 (ROOT/'backups').mkdir()
 check('backup',col.create_backup(backup_folder=str(ROOT/'backups'),force=True,wait_for_completion=True))
 col.close(); col=None
 sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]; sock.close()
 env=os.environ.copy();env.update(SYNC_HOST='127.0.0.1',SYNC_PORT=str(port),SYNC_BASE=str(ROOT/'server'),SYNC_USER1='p0:p0-disposable',RUST_LOG='error')
 log=(ROOT/'server.log').open('w');proc=subprocess.Popen([os.sys.executable,'-m','anki.syncserver'],env=env,stdout=log,stderr=log)
 endpoint=f'http://127.0.0.1:{port}/'
 for _ in range(100):
  try: urllib.request.urlopen(endpoint,timeout=.2)
  except urllib.error.HTTPError: break
  except OSError: time.sleep(.05)
 else: raise RuntimeError('server not ready')
 a=Collection(str(ROOT/'client-a.anki2')); model=a.models.by_name('Basic'); n=a.new_note(model);n['Front']='sync marker';n['Back']='back';a.add_note(n,a.decks.id('Default'));nid=n.id
 a.media.write_data('p0.txt',b'isolated-media')
 auth=a.sync_login('p0','p0-disposable',endpoint)
 out=a.sync_collection(auth,sync_media=False);print('first sync',out,flush=True)
 a.full_upload_or_download(auth=auth,server_usn=None,upload=True);a.sync_media(auth)
 for _ in range(100):
  status=a.media_sync_status()
  if not status.active: break
  time.sleep(.05)
 check('full upload/media',not status.active)
 n=a.get_note(nid);n['Back']='incremental change';a.update_note(n);out=a.sync_collection(auth,sync_media=False);check('incremental sync',out.required==0,int(out.required));a.close()
 b=Collection(str(ROOT/'client-b.anki2'));auth=b.sync_login('p0','p0-disposable',endpoint);out=b.sync_collection(auth,sync_media=False)
 b.full_upload_or_download(auth=auth,server_usn=None,upload=False);b.sync_media(auth)
 for _ in range(100):
  status=b.media_sync_status()
  if not status.active:break
  time.sleep(.05)
 check('second client content',b.get_note(nid)['Back']=='incremental change')
 check('second client media',(pathlib.Path(b.media.dir())/'p0.txt').read_bytes()==b'isolated-media');b.close()
except Exception:
 traceback.print_exc();results.append({'name':'exception','passed':False,'detail':traceback.format_exc()})
finally:
 if col: col.close()
 if proc:proc.terminate();proc.wait(timeout=10)
 report={'root':str(ROOT),'results':results,'passed':all(x['passed'] for x in results)}
 pathlib.Path('/tmp/ir4anki-p0-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if report['passed'] else 1)
