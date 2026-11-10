"""Disposable real owner: idempotent managed notes + external edits + durable undo."""
import asyncio,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from native_fixture import configure,cleanup
from anki_backend import PylibClient,BackendError
async def run(path):
 c=PylibClient(path)
 async def owner(fn):return await c._wait_owner(asyncio.get_running_loop().run_in_executor(c._executor,lambda:fn(c._open())))
 key='a'*32+':'+'b'*32
 fields={'Question':'<img src="q.png">','Answer':'<img src="a.png">','AuthoringKey':key}
 params=dict(key=key,fields=fields,expected_hash=None,deck='2026',suspended=False)
 try:
  a=await c.call('lectureManagedNote',params);b=await c.call('lectureManagedNote',params)
  assert a['note_id']==b['note_id'] and a['card_ids']==b['card_ids'] and len(a['card_ids'])==1
  cid=a['card_ids'][0]
  before=await owner(lambda col:(col.db.all('select * from cards'),col.db.all('select * from revlog')))
  await c.call('answerCards',{'answers':[{'cardId':cid,'ease':3}]})
  token=await c.call('nativeUndoToken',{'card':cid});assert token
  try:await c.call('lectureManagedDelete',dict(key=key,note_id=a['note_id'],expected_hash=a['fields_hash']))
  except BackendError as exc:assert '撤销' in str(exc)
  else:raise AssertionError('pending answer note deleted')
  edited=dict(params,expected_hash=a['fields_hash'],fields=dict(fields,Answer='edited <img src="a.png">'))
  updated=await c.call('lectureManagedNote',edited);assert updated['note_id']==a['note_id']
  new=dict(params,key='c'*32+':'+'d'*32,fields=dict(fields,AuthoringKey='c'*32+':'+'d'*32))
  await c.call('lectureManagedNote',new)
  await c.close();c=PylibClient(path)
  assert await c.call('nativeUndoToken',{'card':cid})==token
  await c.call('nativeUndo',{'card':cid,'token':token})
  after=await owner(lambda col:(col.db.all('select * from cards where id=?',cid),col.db.all('select * from revlog where cid=?',cid)))
  assert after==(before[0],before[1]),'schedule/revlog not restored'
  note=await c.call('notesInfo',{'notes':[a['note_id']]});assert note[0]['fields']['Answer']['value']==edited['fields']['Answer']
  await c.call('updateNoteFields',{'note':{'id':a['note_id'],'fields':{'Answer':'external'}}})
  try:await c.call('lectureManagedNote',dict(edited,expected_hash=updated['fields_hash']))
  except BackendError as exc:assert '外部修改' in str(exc)
  else:raise AssertionError('external edit overwritten')
  current=await c.call('lectureManagedLookup',{'key':key})
  delete=dict(key=key,note_id=a['note_id'],expected_hash=current['fields_hash'])
  assert (await c.call('lectureManagedDelete',delete))['deleted']
  assert (await c.call('lectureManagedDelete',delete))['deleted']
  remaining=await c.call('findCards',{'query':''});assert len(remaining)==1
  print('native managed idempotency + create/edit/restart/undo + conflict passed')
 finally:await c.close()
if __name__=='__main__':
 for k in list(os.environ):
  if k.startswith('ANKI_') or k=='ANKICONNECT_URL':os.environ.pop(k)
 assert os.environ.get('NATIVE_FIXTURE_ROOT'),'explicit scratch root required'
 try:asyncio.run(run(configure()))
 finally:cleanup()
