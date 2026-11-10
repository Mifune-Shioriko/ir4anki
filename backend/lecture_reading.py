"""Typed page ranges: no Markdown line aliases or text heuristics."""
import uuid
from lecture_store import Store,Conflict,identifier,digest,canonical
class PageReading(Store):
 def seed(self,d,r,path,count):
  identifier(d);identifier(r)
  if not 1<=count<=2000:raise ValueError('非法页数')
  with self.db() as db:
   old=next((x for x in self.all('page-segments',db) if x['document_id']==d and x['revision_id']==r and x['parent_segment_id'] is None),None)
   if old:return old
   seg=dict(kind='pdf',document_id=d,revision_id=r,segment_id=uuid.uuid4().hex,path=path,page_start=1,page_end=count,status='todo',version=1,parent_segment_id=None,cards_created=[])
   self.put('page-segments',seg['segment_id'],seg,db);return seg
 def segment(self,s):
  value=self.get('page-segments',identifier(s))
  if not value:raise ValueError('页片段不存在')
  return value
 def split(self,s,start,end,*,expected,request_id):
  fp=digest(canonical([s,start,end,expected]).encode())
  with self.db() as db:
   old=self.get('page-splits',request_id,db)
   if old:
    if old['fingerprint']!=fp:raise Conflict('分割请求身份冲突')
    return old['result']
   seg=self.get('page-segments',identifier(s),db)
   if not seg:raise ValueError('页片段不存在')
   if seg['version']!=expected or seg['status']=='container':raise Conflict('页片段已变化，请重新确认分割')
   if not all(isinstance(n,int) and not isinstance(n,bool) for n in [start,end]) or not seg['page_start']<=start<=end<=seg['page_end']:raise ValueError('分割页码越界')
   if (start,end)==(seg['page_start'],seg['page_end']):raise ValueError('不能按整个原范围分割')
   ranges=[]
   if start>seg['page_start']:ranges.append((seg['page_start'],start-1,'background',False))
   ranges.append((start,end,'todo',False))
   if end<seg['page_end']:ranges.append((end+1,seg['page_end'],'todo',True))
   children=[]
   for a,b,status,tail in ranges:
    child=dict(seg,segment_id=uuid.uuid4().hex,parent_segment_id=s,page_start=a,page_end=b,status=status,version=1,cards_created=[],tail=tail)
    self.put('page-segments',child['segment_id'],child,db);children.append(child)
   seg.update(status='container',version=expected+1);self.put('page-segments',s,seg,db)
   result=dict(parent=seg,children=children,replacements=[x for x in children if x['status']=='todo' and not x['tail']])
   self.put('page-splits',request_id,dict(fingerprint=fp,result=result),db);return result
 def payload(self,seg):
  ancestors=[];cur=seg
  while cur.get('parent_segment_id'):
   cur=self.segment(cur['parent_segment_id']);ancestors+=cur['cards_created']
  revision=self.get('revisions',seg['revision_id']) or {}
  return dict(seg,page_count=revision.get('page_count',seg['page_end']),chunk_key=seg['segment_id'],title=f"第 {seg['page_start']}–{seg['page_end']} 页",heading_path=[],text='',ancestor_cards=ancestors,file_chunks=0,file_done=0,file_skipped=0)
 def deal(self,gated=None,listed=None):
  gated=gated or set();groups={}
  for s in self.all('page-segments'):
   if s['status'] in ('todo','active') and (s['path'],s['segment_id']) not in gated and (listed is None or s['document_id'] in listed):groups.setdefault((s['document_id'],s['revision_id']),[]).append(s)
  result=[]
  for values in groups.values():
   values.sort(key=lambda s:(s['status']!='active',s['page_start']))
   result.extend(self.payload(s) for s in values[:2])
  return result
 def act(self,s,action):
  with self.db() as db:
   seg=self.get('page-segments',identifier(s),db)
   if not seg or seg['status']=='container':raise Conflict('页片段已变化')
   states={'complete':'done','skip':'skipped','mark_active':'active','promote':'todo','demote':'background'}
   if action!='next':
    if action not in states:raise ValueError('未知阅读操作')
    seg.update(status=states[action],version=seg['version']+1);self.put('page-segments',s,seg,db)
  return seg
 def record_card(self,s,n):
  with self.db() as db:
   seg=self.get('page-segments',identifier(s),db)
   if not seg:raise ValueError('来源页片段不存在')
   if n not in seg['cards_created']:seg['cards_created'].append(n)
   if seg['status']=='todo':seg['status']='active'
   self.put('page-segments',s,seg,db)
