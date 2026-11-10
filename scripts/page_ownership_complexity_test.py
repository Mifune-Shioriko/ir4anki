"""Operation-count regression for the real entries assembler, synthetic stores.
No native/app imports; a count budget interrupts the old cubic join early.
"""
import os,sys
from pathlib import Path
sys.path.insert(0,os.environ.get('PAGE_FIX_BACKEND',str(Path(__file__).resolve().parents[1]/'backend')))
from lecture_integration import Lectures
from lecture_store import Conflict
class BudgetDict(dict):
 reads=0
 @classmethod
 def tick(cls):
  cls.reads+=1
  assert cls.reads<300000,'assembly exceeded deterministic segment-field work budget (repeated source/segment join)'
 def __getitem__(self,key):self.tick();return super().__getitem__(key)
 def get(self,key,default=None):self.tick();return super().get(key,default)
class Reading:
 def __init__(self,rows):self.rows=rows;self.loads=0
 def all(self,namespace):assert namespace=='page-segments';self.loads+=1;return self.rows
class Docs:
 def __init__(self,notes):self.notes=notes;self.loads=0
 def list(self):return [dict(document_id='d',path='generated.pdf',current_revision='r',reading_revision='r',listed=True)]
 def all(self,namespace):assert namespace=='source-links';self.loads+=1;return self.notes

def test_single_assembly_resolves_each_source_once():
 # Within the supported 2000 pages: 1000 single-page leaves and 2000 notes.
 root=BudgetDict(document_id='d',revision_id='r',segment_id='root',page_start=1,page_end=1000,status='container',cards_created=list(range(1,2001)))
 leaves=[BudgetDict(document_id='d',revision_id='r',segment_id=f'leaf-{p}',page_start=p,page_end=p,status='todo',cards_created=[],parent_segment_id='root') for p in range(1,1001)]
 sources=[dict(document_id='d',revision_id='r',segment_id='root',page=1+(n-1)%1000,note_id=n) for n in range(1,2001)]
 l=object.__new__(Lectures);l.reading=Reading([root,*leaves]);l.docs=Docs(sources)
 calls=0;resolve=l.effective_source
 def counted(*args,**kwargs):
  nonlocal calls
  calls+=1;assert calls<=len(sources),'source resolved more than once per assembly'
  return resolve(*args,**kwargs)
 l.effective_source=counted;BudgetDict.reads=0
 assembled=l.entries();assert l.reading.loads==1 and l.docs.loads==1
 assert calls==2000
 by={s['segment_id']:s for s in assembled[0]['segments']}
 assert by['leaf-3']['cards_created']==[3,1003]
 assert by['root']['cards_created']==list(range(1,2001))
 print('segments=1001 notes=2000 resolver_calls=',calls,'segment_field_reads=',BudgetDict.reads)

def test_index_keeps_ambiguous_missing_and_revision_diagnostics():
 root=dict(document_id='d',revision_id='r',segment_id='root',page_start=1,page_end=3,status='container',cards_created=[1,2,3])
 leaves=[dict(root,segment_id='a',status='todo',page_start=1,page_end=1,cards_created=[]),dict(root,segment_id='b',status='todo',page_start=1,page_end=2,cards_created=[])]
 l=object.__new__(Lectures);l.reading=Reading([root,*leaves])
 valid=dict(document_id='d',revision_id='r',segment_id='root',page=2,note_id=1)
 l.docs=Docs([valid,dict(valid,page=1,note_id=2),dict(valid,page=3,note_id=3),dict(valid,page=None,note_id=4),dict(valid,segment_id='absent',note_id=5),dict(valid,revision_id='other',note_id=6)])
 assembled=l.entries()[0];by={s['segment_id']:s for s in assembled['segments']}
 assert by['a']['cards_created']==[] and by['b']['cards_created']==[1]
 assert by['root']['cards_created']==[1,2,3]
 assert {e['note_id'] for e in assembled['ownership_errors']}=={2,3,4,5}
 ownership=l.ownership_index([root,*leaves])
 for bad,word in [(dict(valid,page=1),'不唯一'),(dict(valid,page=3),'没有有效'),(dict(valid,page=None),'版本或页'),(dict(valid,segment_id='absent'),'历史'),(dict(valid,revision_id='other'),'版本或页')]:
  try:l.effective_source(bad,ownership=ownership)
  except Conflict as exc:assert word in str(exc),str(exc)
  else:raise AssertionError('unresolved ownership guessed')

if __name__=='__main__':
 test_single_assembly_resolves_each_source_once()
 test_index_keeps_ambiguous_missing_and_revision_diagnostics()
