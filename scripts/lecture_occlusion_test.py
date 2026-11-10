import os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from lecture_occlusion import Authoring,rectangle,pixel_box
from lecture_store import Conflict,digest
class OcclusionTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir=os.environ['NATIVE_FIXTURE_ROOT']);self.a=Authoring(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_rounding_and_crop_guards(self):
  self.assertEqual(pixel_box([.11,.22,.1,.1],101,99),(11,21,22,32))
  for x in [[-.1,0,1,1],[0,0,0,1],[0,0,float('nan'),1],[.9,0,.2,.2]]:
   with self.assertRaises(ValueError):rectangle(x)
 def test_draft_optimism_and_stable_ids(self):
  draft=self.a.create(dict(document_id='a'*32,revision_id='b'*64,page=1,segment_id='c'*32),dict(raster_hash='d'*64,path='immutable.png',width=100,height=100,render_profile='test'))
  mask={'mask_id':'e'*32,'rect':[.1,.1,.2,.2]}
  saved=self.a.save(draft['authoring_id'],dict(draft,masks=[mask]),expected=1)
  self.assertEqual(saved['masks'][0]['mask_id'],mask['mask_id'])
  with self.assertRaises(Conflict):self.a.save(draft['authoring_id'],draft,expected=1)
  with self.assertRaises(ValueError):self.a.save(draft['authoring_id'],dict(saved,crop=[.5,.5,.1,.1]),expected=2)
 def test_stage_before_effect_recovery_and_request_collision(self):
  a=self.a.create(dict(document_id='a'*32,revision_id='b'*64,page=1,segment_id='c'*32),dict(raster_hash='d'*64,path='immutable.png',width=100,height=100,render_profile='test'))
  a=self.a.save(a['authoring_id'],dict(a,masks=[dict(mask_id='e'*32,rect=[.1,.1,.2,.2])]),expected=1)
  op=self.a.stage(a['authoring_id'],a['version'],'req',[])
  self.assertEqual(Authoring(self.tmp.name).operation(op['operation_id'])['status'],'pending')
  with self.assertRaises(Conflict):self.a.stage(a['authoring_id'],1,'req',[])
 def test_last_mask_deletion_requires_exact_confirmation(self):
  draft=self.a.create(dict(document_id='a'*32,revision_id='b'*64,page=1,segment_id='c'*32),dict(raster_hash='d'*64,path='immutable.png',width=100,height=100,render_profile='test'))
  key=draft['authoring_id']+':'+'e'*32
  self.a.put('occlusion-links',key,dict(authoring_id=draft['authoring_id'],mask_id='e'*32,note_id=123,fields_hash='f'*64,deleted=False))
  with self.assertRaises(Conflict):self.a.stage(draft['authoring_id'],1,'unconfirmed',[])
  op=self.a.stage(draft['authoring_id'],1,'confirmed',[123])
  self.assertEqual([(x['action'],x['note_id']) for x in op['items']],[('delete',123)])
if __name__=='__main__':unittest.main()
