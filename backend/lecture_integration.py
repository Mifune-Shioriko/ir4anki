"""Adapter into existing reading/flow state, including interrupted splits."""
from lecture_store import Conflict
from lecture_documents import Documents
from lecture_reading import PageReading
from lecture_annotations import Annotations
from lecture_occlusion import Authoring
class Lectures:
 def __init__(self,root):
  self.docs=Documents(root);self.reading=PageReading(root);self.annotations=Annotations(root);self.authoring=Authoring(root)
 def entries(self):
  stored_segments=self.reading.all('page-segments');sources=self.docs.all('source-links')
  ownership=self.ownership_index(stored_segments)
  by_revision={};notes_by_leaf={};errors_by_revision={}
  for seg in stored_segments:by_revision.setdefault((seg['document_id'],seg['revision_id']),[]).append(seg)
  # Resolve every source once against one shared identity/page index. Historical
  # container lists stay intact; effective lists exist only in this assembly.
  for source in sources:
   try:
    effective=self.effective_source(source,ownership=ownership)
    notes_by_leaf.setdefault(effective['segment_id'],set()).add(source['note_id'])
   except (ValueError,Conflict) as exc:
    errors_by_revision.setdefault((source.get('document_id'),source.get('revision_id')),[]).append(dict(note_id=source.get('note_id'),message=str(exc)))
  result=[]
  for doc in sorted(self.docs.list(),key=lambda d:d.get('position',10**9)):
   if not doc.get('listed'):continue
   rev=doc.get('reading_revision',doc['current_revision']);key=(doc['document_id'],rev)
   segments=[dict(seg,path=doc['path'],cards_created=list(seg['cards_created']) if seg['status']=='container' else sorted(notes_by_leaf.get(seg['segment_id'],set()))) for seg in by_revision.get(key,[])]
   result.append(dict(kind='pdf',document_id=doc['document_id'],revision_id=rev,path=doc['path'],segments=segments,ownership_errors=errors_by_revision.get(key,[]),position=doc.get('position',10**9)))
  return result
 def merge(self,data):
  for entry in self.entries():data['list'].insert(min(entry['position'],len(data['list'])),entry)
  self.repair_pending(data)
  return data
 def persist_list(self,data):
  entries={e['document_id']:(i,e) for i,e in enumerate(data.get('list',[])) if e.get('kind')=='pdf'}
  with self.docs.db() as db:
   for doc in self.docs.all('documents',db):
    if doc['document_id'] in entries:
     pos,e=entries[doc['document_id']];doc.update(listed=True,position=pos,reading_revision=e['revision_id'])
    else:doc['listed']=False
    self.docs.put('documents',doc['document_id'],doc,db)
 def view(self,entry,gated=None):
  gated=gated or set();path=entry['path'];segs=sorted((s for s in entry['segments'] if s['status']!='container'),key=lambda s:s['page_start'])
  ordered=[(self.reading.payload(s),s['segment_id'],s) for s in segs]
  available=[p for p,k,s in ordered if s['status'] in ('todo','active') and (path,k) not in gated]
  held=[p for p,k,s in ordered if s['status'] in ('todo','active') and (path,k) in gated]
  summary=dict(kind='pdf',path=path,title=path.rsplit('/',1)[-1],total_chunks=len(segs),frontier=available[0] if available else None,gated_frontier=held[0] if held else None,gated=len(held),gated_active=sum(x['status']=='active' for x in held),cards_created=sum(len(s['cards_created']) for s in segs),orphans=0)
  summary.update({k:sum(s['status']==k for s in segs) for k in ['todo','active','done','skipped','background']})
  return ordered,summary
 def repair_pending(self,data):
  rd=data.get('round')
  if not rd or rd.get('status')!='active':return
  # Fixed point handles splitting a replacement before an older intent was
  # acknowledged. Unread tails never enter this round.
  for _ in range(64):
   changed=False;pending=[]
   for item in rd.get('pending',[]):
    key=item.get('chunk_key');seg=self.reading.get('page-segments',key)
    split=next((x['result'] for x in self.reading.all('page-splits') if x['result']['parent']['segment_id']==key),None)
    if seg and split:
     pending.extend(dict(path=item['path'],chunk_key=c['segment_id']) for c in split['replacements']);changed=True
    else:pending.append(item)
   rd['pending']=pending
   if not changed:break
 def repair_flow(self,flow):
  if not flow or 'slots' not in flow:return
  from flow import replace_slot
  for split in self.reading.all('page-splits'):
   result=split['result'];parent=result['parent']
   doc=self.docs.document(parent['document_id']);path=doc['path']
   # replace_slot intentionally keeps a parent alias, avoid duplicate aliases
   children=[dict(path=path,chunk_key=c['segment_id']) for c in result['replacements']]
   p=dict(path=path,chunk_key=parent['segment_id'])
   for slot in flow['slots']:
    if [path,parent['segment_id']] in slot and not all([path,c['chunk_key']] in slot for c in children):replace_slot(flow,p,children);break
 def page_notes(self,d,r,page):
  # Filter immutable exact locators before any details cap. No ancestor union.
  return sorted({int(x['note_id']) for x in self.docs.all('source-links') if x.get('document_id')==d and x.get('revision_id')==r and x.get('page')==page and x.get('note_id',0)>0})
 def ownership_index(self,segments):
  # Page coordinates are bounded (<=2000 per document). Valid leaf trees visit
  # each page once; overlapping leaves retain an explicit ambiguous sentinel.
  identities={};owners={}
  for seg in segments:
   identities[seg['segment_id']]=seg
   if seg['status']=='container':continue
   d,r=seg['document_id'],seg['revision_id']
   for page in range(seg['page_start'],seg['page_end']+1):
    key=(d,r,page)
    owners[key]=None if key in owners else seg['segment_id']
  return identities,owners
 def effective_source(self,source,segments=None,*,ownership=None):
  # History is immutable; current association is derived, never a migration.
  if ownership is None:ownership=self.ownership_index(self.reading.all('page-segments') if segments is None else segments)
  identities,owners=ownership;seg=identities.get(source.get('segment_id'))
  if not seg:raise Conflict('历史来源片段不存在；不会自动迁移')
  page=source.get('page')
  if not isinstance(page,int) or isinstance(page,bool) or any(seg[k]!=source.get(k) for k in ['document_id','revision_id']) or not seg['page_start']<=page<=seg['page_end']:raise Conflict('来源文档、版本或页与历史片段不匹配')
  key=(source['document_id'],source['revision_id'],page)
  if key not in owners:raise Conflict('来源页没有有效叶片段，请核对阅读树；不会自动迁移')
  if owners[key] is None:raise Conflict('来源页覆盖叶片段不唯一，请核对阅读树；不会自动迁移')
  return dict(source,segment_id=owners[key])
 def record(self,note,source):
  effective=self.effective_source(source)
  old=self.docs.get('source-links',str(note))
  if old and any(old.get(k)!=source.get(k) for k in ['document_id','revision_id','page']):raise Conflict('已有笔记来源页不可静默改挂')
  if not old:
   self.reading.record_card(source['segment_id'],note)
   self.docs.put('source-links',str(note),dict(source,kind='pdf',note_id=note))
  # New authoring activates only its effective leaf. Historical lists stay put.
  leaf=self.reading.segment(effective['segment_id'])
  if leaf['status']=='todo':self.reading.act(leaf['segment_id'],'mark_active')
 def source(self,note):
  src=self.docs.get('source-links',str(note))
  if not src:return None
  seg=self.reading.segment(src['segment_id']);doc=self.docs.document(src['document_id']);payload=self.reading.payload(dict(seg,path=doc['path']))
  trail=[];cur=seg
  while cur:
   trail.insert(0,dict(segment_id=cur['segment_id'],title=f"第 {cur['page_start']}–{cur['page_end']} 页",status=cur['status'],cards_created=cur['cards_created']))
   cur=self.reading.segment(cur['parent_segment_id']) if cur['parent_segment_id'] else None
  link=next((x for x in self.authoring.all('occlusion-links') if x.get('note_id')==note and not x.get('deleted')),None)
  try:effective=self.effective_source(src);ownership_error=None
  except (ValueError,Conflict) as exc:effective=None;ownership_error=str(exc)
  effective_chunk=self.reading.payload(dict(self.reading.segment(effective['segment_id']),path=doc['path'])) if effective else None
  return dict(payload,effective_chunk=effective_chunk,effective_source=effective,ownership_error=ownership_error,**{k:v for k,v in src.items() if k not in payload},breadcrumb=trail,stale=False,authoring_id=link['authoring_id'] if link else None)
