"""Deterministic Store tests. Synthetic note IDs; no Anki/app import."""
import sys,os,tempfile,traceback
from pathlib import Path
sys.path.insert(0,os.environ.get('PAGE_FIX_BACKEND',str(Path(__file__).resolve().parents[1]/'backend')))
from lecture_integration import Lectures
from lecture_store import Conflict

def seed(tmp_path):
 l=Lectures(tmp_path);d='d'*32;r='e'*32
 l.docs.put('documents',d,dict(document_id=d,path='fixture.pdf',current_revision=r,reading_revision=r,listed=True))
 l.docs.put('revisions',r,dict(document_id=d,revision_id=r,page_count=20))
 root=l.reading.seed(d,r,'fixture.pdf',20)
 return l,root

def src(s,p):return dict(kind='pdf',document_id=s['document_id'],revision_id=s['revision_id'],segment_id=s['segment_id'],page=p)

def test_exact_peers_before_cap_and_after_split(tmp_path):
 l,s=seed(tmp_path)
 for n in range(1,121):l.record(n,src(s,4))
 l.record(200,src(s,3));l.record(200,src(s,3))
 assert l.page_notes(s['document_id'],s['revision_id'],3)==[200]
 assert len(l.page_notes(s['document_id'],s['revision_id'],4))==120
 assert l.page_notes(s['document_id'],s['revision_id'],2)==[]
 split=l.reading.split(s['segment_id'],3,4,expected=1,request_id='split')
 assert l.page_notes(s['document_id'],s['revision_id'],3)==[200]
 assert l.source(200)['segment_id']==s['segment_id'] # immutable history
 assert l.source(200)['effective_source']['segment_id']==split['replacements'][0]['segment_id']
 assert l.page_notes('a'*32,s['revision_id'],3)==[]
 assert l.page_notes(s['document_id'],'a'*32,3)==[]

def test_effective_gate_membership_and_origin(tmp_path):
 l,s=seed(tmp_path);l.record(1,src(s,3));l.record(2,src(s,4))
 split=l.reading.split(s['segment_id'],3,3,expected=1,request_id='split')
 entry=l.entries()[0];selected=split['replacements'][0];tail=split['children'][-1]
 by={x['segment_id']:x for x in entry['segments']}
 assert by[selected['segment_id']]['cards_created']==[1]
 assert by[tail['segment_id']]['cards_created']==[2]
 l.record(1,src(selected,3))
 assert l.docs.get('source-links','1')['segment_id']==s['segment_id']
 assert l.reading.segment(s['segment_id'])['cards_created']==[1,2]
 try:l.record(1,src(tail,4))
 except Conflict:pass
 else:raise AssertionError('silently retargeted existing note to another page')

def test_recursive_slot_and_deferred_tail(tmp_path):
 l,s=seed(tmp_path)
 one=l.reading.split(s['segment_id'],3,8,expected=1,request_id='one')
 two=l.reading.split(one['replacements'][0]['segment_id'],3,4,expected=1,request_id='two')
 pending=dict(round=dict(status='active',pending=[dict(path='fixture.pdf',chunk_key=s['segment_id'])]))
 l.repair_pending(pending);l.repair_pending(pending)
 assert [x['chunk_key'] for x in pending['round']['pending']]==[two['replacements'][0]['segment_id']]
 flow=dict(slots=[[['fixture.pdf',s['segment_id']]]]);l.repair_flow(flow);l.repair_flow(flow)
 assert len(flow['slots'])==1 and len(flow['slots'][0])==3
 assert len({tuple(a) for a in flow['slots'][0]})==3


def test_absent_or_ambiguous_leaf_is_explicit(tmp_path):
 l,s=seed(tmp_path)
 assert l.effective_source(src(s,3))['segment_id']==s['segment_id']
 l.reading.put('page-segments','f'*32,dict(s,segment_id='f'*32))
 try:l.effective_source(src(s,3))
 except Conflict:pass
 else:raise AssertionError('ambiguous leaf guessed')
 try:l.effective_source(dict(src(s,3),revision_id='a'*32))
 except (ValueError,Conflict):pass
 else:raise AssertionError('absent revision migrated')

def test_first_last_inclusive_and_stale_boundaries(tmp_path):
 for i,(a,z) in enumerate([(1,1),(1,4),(17,20),(20,20)]):
  l,root=seed(tmp_path/str(i))
  result=l.reading.split(root['segment_id'],a,z,expected=1,request_id='edge')
  assert [(c['page_start'],c['page_end']) for c in result['replacements']]==[(a,z)]
  assert result['parent']['page_start']==1 and result['parent']['page_end']==20
  if a==1:assert result['children'][0]['status']=='todo'
  if z==20:assert not any(c['tail'] for c in result['children'])
  assert l.reading.split(root['segment_id'],a,z,expected=1,request_id='edge')==result
  try:l.reading.split(root['segment_id'],a,z,expected=1,request_id='stale')
  except Conflict:pass
  else:raise AssertionError('historical parent accepted again')

if __name__=='__main__':
 failures=0
 for name,fn in list(globals().items()):
  if name.startswith('test_'):
   try:
    with tempfile.TemporaryDirectory(dir=os.environ['NATIVE_FIXTURE_ROOT']) as tmp:fn(Path(tmp))
    print('PASS',name)
   except Exception:
    failures+=1;print('FAIL',name);traceback.print_exc()
 raise SystemExit(bool(failures))
