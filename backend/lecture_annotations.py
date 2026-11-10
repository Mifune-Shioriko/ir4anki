"""Real UTF-8 page Markdown with durable replace intents and optimistic edits.

The filesystem is authoritative. Index records never contain a second editable
body. Prepared full snapshots are temporary recovery intents, retained until
completion. A crash after replace is distinguished from external modification.
"""
from pathlib import Path
from lecture_store import Store, Conflict, atomic, canonical, digest, identifier

class Annotations(Store):
 def __init__(self,root):
  super().__init__(root);self.recover()
 def _key(self,document,revision,page):
  identifier(document);identifier(revision)
  if not isinstance(page,int) or isinstance(page,bool) or page<1:raise ValueError('非法页码')
  return f'{document}/{revision}/{page}'
 def _path(self,key):return self.root/'page-notes'/f'{key}.md'
 def _read(self,key,db):
  path=self._path(key)
  if path.exists() and path.stat().st_size>2*1024*1024:raise ValueError('外部页笔记超出 2 MiB 限制；原文件保留')
  data=path.read_bytes() if path.exists() else b''
  body=data.decode('utf-8',errors='strict');aid=digest(key.encode())
  result=dict(annotation_id=aid,version=digest(data),body=body,markdown_path=str(path))
  self.put('annotations',key,{k:v for k,v in result.items() if k!='body'},db)
  return result
 def read(self,document,revision,page):
  key=self._key(document,revision,page)
  with self.db() as db:return self._read(key,db)
 def _apply(self,intent,db,fault=lambda phase:None):
  key=intent['key'];path=self._path(key);current=digest(path.read_bytes() if path.exists() else b'')
  if current not in (intent['expected'],intent['hash']):raise Conflict('页笔记已被外部修改，保留草稿并重新合并')
  if current!=intent['hash']:atomic(path,intent['body'].encode('utf-8'))
  fault('replaced')
  result=self._read(key,db);intent=dict(intent,state='complete',result=result)
  intent.pop('body',None);self.put('annotation-intents',intent['request'],intent,db)
  return result
 def recover(self):
  for intent in self.all('annotation-intents'):
   if intent['state']=='prepared':
    try:
     with self.db() as db:self._apply(intent,db)
    except Conflict:pass # surface conflict; never overwrite external bytes
 def search(self,document,query,limit=30):
  identifier(document)
  if not isinstance(query,str) or not 1<=len(query)<=200:raise ValueError('请输入 1–200 个字符的页笔记搜索词')
  limit=max(1,min(int(limit),50));result=[]
  with self.db() as db:
   keys=[r[0] for r in db.execute('SELECT key FROM lecture_objects WHERE namespace=? AND key LIKE ? ORDER BY key',('annotations',document+'/%'))]
   for key in keys:
    value=self._read(key,db);at=value['body'].casefold().find(query.casefold())
    if at>=0:
     d,r,page=key.split('/')
     result.append(dict(document_id=d,revision_id=r,page=int(page),annotation_id=value['annotation_id'],excerpt=value['body'][max(0,at-40):at+140]))
     if len(result)>=limit:break
  return result
 def save(self,document,revision,page,*,body,expected,request_id,fault=lambda phase:None):
  key=self._key(document,revision,page)
  if not isinstance(body,str) or len(body.encode('utf-8'))>2*1024*1024:raise ValueError('附注超出 2 MiB 限制')
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('缺少请求身份')
  fingerprint=digest(canonical([key,body,expected]).encode())
  with self.db() as db:
   intent=self.get('annotation-intents',request_id,db)
   if intent:
    if intent['fingerprint']!=fingerprint:raise Conflict('请求身份已用于不同内容')
    if intent['state']=='complete':return intent['result']
   else:
    if self._read(key,db)['version']!=expected:raise Conflict('页笔记版本冲突，请保留草稿重新合并')
    intent=dict(request=request_id,key=key,body=body,expected=expected,hash=digest(body.encode()),fingerprint=fingerprint,state='prepared')
    self.put('annotation-intents',request_id,intent,db)
  fault('prepared')
  with self.db() as db:return self._apply(intent,db,fault)
