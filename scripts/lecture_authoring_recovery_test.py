"""Real media/native effects with injected failures after side effects."""
import asyncio,os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from native_fixture import configure,cleanup
from lecture_documents import Documents
from lecture_occlusion import Authoring
from lecture_real_documents_test import make_pdf
from anki_backend import PylibClient
class Recovery(unittest.TestCase):
 def test_effect_recovery_and_external_field_conflict(self):
  path=configure();root=Path(os.environ['ANKI_STATE_DIR'])/'lectures';docs=Documents(root);a=Authoring(root)
  rev=docs.import_bytes('generated.pdf',make_pdf());raster=docs.raster(rev['document_id'],rev['revision_id'],1)
  draft=a.create(dict(kind='pdf',document_id=rev['document_id'],revision_id=rev['revision_id'],page=1,segment_id='c'*32),raster)
  draft=a.save(draft['authoring_id'],dict(draft,masks=[dict(mask_id='d'*32,rect=[.1,.1,.2,.2]),dict(mask_id='e'*32,rect=[.5,.5,.2,.2])]),expected=1)
  async def run():
   c=PylibClient(path)
   async def heartbeat():
    while True:await asyncio.sleep(.01)
   ticker=asyncio.create_task(heartbeat())
   async def source(*args):pass
   try:
    for index,phase in enumerate(['media','note','item']):
     draft=a.create(dict(kind='pdf',document_id=rev['document_id'],revision_id=rev['revision_id'],page=1,segment_id='c'*32),raster)
     draft=a.save(draft['authoring_id'],dict(draft,masks=[dict(mask_id='d'*32,rect=[.1,.1,.2,.2]),dict(mask_id='e'*32,rect=[.5,.5,.2,.2])]),expected=1)
     operation=a.stage(draft['authoring_id'],draft['version'],'publish'+phase,[])
     hit=False
     def fault(p):
      nonlocal hit
      if p==phase and not hit:hit=True;raise RuntimeError('fault '+phase)
     try:result=await a.publish(operation['operation_id'],c.call,deck='2026',suspended=False,record_source=source,fault=fault)
     except RuntimeError:pass
     # New instance = process state reconstructed solely from persisted intent.
     recovered=Authoring(root)
     result=await recovered.publish(operation['operation_id'],c.call,deck='2026',suspended=False,record_source=source)
     self.assertEqual(result['status'],'complete',result)
     ids=await c.call('findCards',{'query':'"note:ir4anki Image QA"'});self.assertEqual(len(ids),2*(index+1))
     # A subsequent publication uses its exact local mapping baseline.
    links=[x for x in a.all('occlusion-links') if x['authoring_id']==draft['authoring_id']];nid=links[0]['note_id'];await c.call('updateNoteFields',{'note':{'id':nid,'fields':{'Answer':'external edit'}}})
    latest=a.draft(draft['authoring_id']);latest=a.save(latest['authoring_id'],dict(latest,answer='new answer'),expected=latest['version'])
    op=a.stage(latest['authoring_id'],latest['version'],'conflict',[]);result=await a.publish(op['operation_id'],c.call,deck='2026',suspended=False,record_source=source)
    self.assertEqual(result['status'],'partial');self.assertTrue(any('外部修改' in (x.get('error') or '') for x in result['items']))
   finally:await c.close();ticker.cancel()
  # A timer handle remains active through default-executor shutdown. The
  # sandbox can suppress selector self-pipe wakeups even after workers exit.
  loop=asyncio.new_event_loop()
  def tick():loop.call_later(.01,tick)
  tick()
  try:
   loop.run_until_complete(run())
   loop.run_until_complete(loop.shutdown_asyncgens())
   loop.run_until_complete(loop.shutdown_default_executor())
  finally:loop.close()
if __name__=='__main__':
 try:unittest.main()
 finally:cleanup()
