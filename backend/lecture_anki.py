"""Managed actions executed only inside PylibClient's existing owner thread."""
import re
from lecture_store import canonical,digest
MODEL='ir4anki Image QA'
FIELDS=['Question','Answer','AuthoringKey']
QFMT='{{Question}}'
AFMT='{{Answer}}'

def fields_hash(fields):return digest(canonical(fields).encode())

def managed(owner,action,p):
 from anki_backend import BackendError
 col=owner._open();key=p['key']
 if not re.fullmatch('[a-f0-9]{32}:[a-f0-9]{32}',key):raise BackendError('非法 AuthoringKey')
 model=col.models.by_name(MODEL)
 if model is None:
  if action!='lectureManagedNote':raise BackendError('受管模型不存在')
  model=col.models.new(MODEL)
  for name in FIELDS:col.models.add_field(model,col.models.new_field(name))
  template=col.models.new_template('Card 1');template.update(qfmt=QFMT,afmt=AFMT);col.models.add_template(model,template)
  model['css']='.card { font-family: sans-serif; text-align: center; } img { max-width:100%; height:auto; }'
  col.models.add(model)
 if model['type']!=0 or [f['name'] for f in model['flds']]!=FIELDS or len(model['tmpls'])!=1 or model['tmpls'][0]['qfmt']!=QFMT or model['tmpls'][0]['afmt']!=AFMT:raise BackendError('受管模型已被外部修改，拒绝生成多卡或不兼容内容')
 ids=col.find_notes(f'"note:{MODEL}" "AuthoringKey:{key}"')
 ids=[nid for nid in ids if col.get_note(nid)['AuthoringKey']==key]
 if len(ids)>1:raise BackendError('AuthoringKey 重复，需要人工处理')
 note=col.get_note(ids[0]) if ids else None
 if action=='lectureManagedLookup':
  if note is None:return None
  return dict(note_id=note.id,card_ids=list(col.find_cards(f'nid:{note.id}')),fields_hash=fields_hash({k:note[k] for k in FIELDS}))
 if action=='lectureManagedDelete':
  if note is None:
   # A prior deletion can have committed before the operation result.
   if p.get('note_id'):
    from anki.errors import NotFoundError
    try:col.get_note(p['note_id'])
    except NotFoundError:return dict(deleted=True,note_id=p['note_id'])
   raise BackendError('笔记身份已变化，不能确认删除')
  if note.id!=p['note_id'] or fields_hash({k:note[k] for k in FIELDS})!=p['expected_hash']:raise BackendError('删除身份或内容冲突')
  journal=owner._journal.data
  if journal and (journal.get('nid') or col.get_card(journal['cid']).nid)==note.id:raise BackendError('此卡仍有答题撤销位置，请先撤销或明确结束撤销后再删除')
  col.remove_notes([note.id]);return dict(deleted=True,note_id=note.id)
 fields=p['fields']
 if set(fields)!=set(FIELDS) or fields['AuthoringKey']!=key:raise BackendError('受管字段不匹配')
 desired=fields_hash(fields)
 created=note is None
 if note:
  before=fields_hash({k:note[k] for k in FIELDS})
  if before!=desired and before!=p.get('expected_hash'):raise BackendError('Anki 字段已被外部修改，草稿保留，请手工合并')
  if before!=desired:
   for name,value in fields.items():note[name]=value
   col.update_note(note)
 else:
  if p.get('expected_hash'):raise BackendError('原受管笔记已被删除，不能静默重建')
  note=col.new_note(model)
  for name,value in fields.items():note[name]=value
  col.add_note(note,col.decks.id(p['deck']))
 cards=list(col.find_cards(f'nid:{note.id}'))
 if len(cards)!=1:raise BackendError('受管笔记不是恰好一张卡，需人工检查')
 # Resume initial routing even when the first dispatch stopped after add_note.
 # Do not route an existing published card again, preserving its schedule.
 if p.get('expected_hash') is None:
  col.set_deck(cards,col.decks.id(p['deck']))
  if p['suspended']:col.sched.suspend_cards(cards)
 return dict(note_id=note.id,card_ids=cards,fields_hash=desired,created=created)
