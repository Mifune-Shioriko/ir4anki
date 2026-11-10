import sys,os,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from lecture_reading import PageReading
from lecture_store import Conflict
from flow import create,replace_slot,reconcile
class ReadingTests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory(dir=os.environ['NATIVE_FIXTURE_ROOT']);self.r=PageReading(self.t.name);self.d='d'*32;self.v='e'*32
 def tearDown(self):self.t.cleanup()
 def test_split_tail_parent_citations_and_restart(self):
  root=self.r.seed(self.d,self.v,'lecture.pdf',12)
  before=self.r.segment(root['segment_id'])
  self.assertEqual(before,root) # opening neutral
  result=self.r.split(root['segment_id'],3,6,expected=1,request_id='split')
  self.assertEqual([(x['page_start'],x['page_end'],x['status']) for x in result['children']],[(1,2,'background'),(3,6,'todo'),(7,12,'todo')])
  self.assertTrue(result['children'][-1]['tail']);self.assertEqual(len(result['replacements']),1)
  self.assertEqual(self.r.segment(root['segment_id'])['page_end'],12)
  self.assertEqual(PageReading(self.t.name).split(root['segment_id'],3,6,expected=1,request_id='split'),result)
  with self.assertRaises(Conflict):self.r.split(root['segment_id'],2,4,expected=1,request_id='other')
  self.assertNotIn('start_line',result['children'][1]);self.assertNotIn('end_line',result['children'][1])
 def test_mixed_flow_slot_and_future_tail(self):
  root=self.r.seed(self.d,self.v,'lecture.pdf',8);pdf=self.r.payload(root);md={'path':'note.md','chunk_key':'1'}
  f=create([md,pdf],[1,2,3,4],'r','c')
  result=self.r.split(root['segment_id'],2,4,expected=1,request_id='a')
  children=[self.r.payload(x) for x in result['replacements']];replace_slot(f,pdf,children)
  reconcile(f,[md]+children,[1,2,3,4]);self.assertEqual(f['reading_consumed'],0)
  reconcile(f,children,[3,4],answered_count=2);self.assertEqual(f['phase'],'reading')
  reconcile(f,[],[3,4],answered_count=2);self.assertEqual(f['phase'],'review')
 def test_card_gate_and_status(self):
  a=self.r.seed(self.d,self.v,'a.pdf',4);self.r.record_card(a['segment_id'],123)
  self.assertEqual(self.r.deal({('a.pdf',a['segment_id'])}),[])
  self.assertEqual(self.r.segment(a['segment_id'])['status'],'active')
  self.r.act(a['segment_id'],'complete');self.assertEqual(self.r.deal(),[])
if __name__=='__main__':unittest.main()
