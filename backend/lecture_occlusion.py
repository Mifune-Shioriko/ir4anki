"""Editable page-coordinate masks and staged portable Basic publication."""
import base64
import html
import io
import math
import uuid
from lecture_store import Store,Conflict,identifier,canonical,digest

def rectangle(rect):
 if not isinstance(rect,list) or len(rect)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in rect):raise ValueError('非法矩形')
 x,y,w,h=rect
 if min(x,y)<0 or min(w,h)<=0 or x+w>1+1e-9 or y+h>1+1e-9:raise ValueError('矩形超出页面')
 return rect

def contains(outer,inner):
 x,y,w,h=outer;a,b,c,d=inner
 return x-1e-9<=a and y-1e-9<=b and a+c<=x+w+1e-9 and b+d<=y+h+1e-9

def pixel_box(rect,w,h):
 x,y,r,b=rectangle(rect)
 return (max(0,math.floor(x*w)),max(0,math.floor(y*h)),min(w,math.ceil((x+r)*w)),min(h,math.ceil((y+b)*h)))

def safe_text(markdown):
 # Raw HTML remains disabled. Only math delimiters and media basenames are
 # normalized for portable native Anki HTML; code spans/fences are untouched.
 from markdown_it import MarkdownIt
 from urllib.parse import unquote
 md=MarkdownIt('commonmark',{'html':False,'linkify':False})
 def math_rule(state,silent):
  start=state.pos;src=state.src;opening=None
  for left,right,display in [('$$','$$',True),('\\[','\\]',True),('\\(','\\)',False),('$','$',False)]:
   if src.startswith(left,start):opening=(left,right,display);break
  if not opening:return False
  left,right,display=opening;end=src.find(right,start+len(left))
  if end<0:return False
  if not silent:
   token=state.push('lecture_math','',0);token.content=src[start+len(left):end];token.meta={'display':display}
  state.pos=end+len(right);return True
 md.inline.ruler.before('escape','lecture_math',math_rule)
 md.renderer.rules['lecture_math']=lambda tokens,index,options,env: ('\\[' if tokens[index].meta['display'] else '\\(')+html.escape(tokens[index].content)+('\\]' if tokens[index].meta['display'] else '\\)')
 image_rule=md.renderer.rules['image']
 def image(tokens,index,options,env):
  token=tokens[index];src=token.attrGet('src') or ''
  for prefix in ['/media/','/api/reading/media/']:
   if src.startswith(prefix):
    name=unquote(src[len(prefix):])
    if not name or '/' in name or '\\' in name:raise ValueError('图片媒体名不合法')
    token.attrSet('src',name)
  return image_rule(tokens,index,options,env)
 md.renderer.rules['image']=image
 return md.render(markdown)

def compose(draft,target):
 from PIL import Image,ImageDraw
 from pathlib import Path
 path=Path(draft['raster']['path']);data=path.read_bytes()
 if digest(data)!=draft['raster']['raster_hash']:raise Conflict('基准位图已变化，不能导出旧草稿')
 img=Image.open(io.BytesIO(data)).convert('RGB');w,h=img.size
 if (w,h)!=(draft['raster']['width'],draft['raster']['height']):raise Conflict('基准位图尺寸冲突')
 result=[]
 for back in [False,True]:
  frame=img.copy();draw=ImageDraw.Draw(frame)
  for mask in draft['masks']:
   current=mask['mask_id']==target
   hidden=(draft['mode']=='all' or current) and not (back and current)
   a,b,c,d=pixel_box(mask['rect'],w,h)
   if hidden:
    draw.rectangle((a,b,c-1,d-1),fill=(32,32,32))
    if current:draw.rectangle((a,b,c-1,d-1),outline=(255,255,255),width=max(2,w//350))
   elif current:draw.rectangle((a,b,c-1,d-1),outline=(32,32,32),width=max(2,w//350))
  frame=frame.crop(pixel_box(draft['crop'],w,h));out=io.BytesIO();frame.save(out,format='PNG');result.append(out.getvalue())
 return result

def crop_image(raster,crop):
 from pathlib import Path
 from PIL import Image
 data=Path(raster['path']).read_bytes()
 if digest(data)!=raster['raster_hash']:raise Conflict('基准图已变化')
 image=Image.open(io.BytesIO(data)).convert('RGB')
 image=image.crop(pixel_box(crop,image.width,image.height));out=io.BytesIO();image.save(out,'PNG');return out.getvalue()

class Authoring(Store):
 def create(self,source,raster):
  identifier(source['document_id']);identifier(source['revision_id']);identifier(source['segment_id'])
  if not isinstance(source['page'],int) or source['page']<1:raise ValueError('非法来源页')
  a=uuid.uuid4().hex
  draft=dict(authoring_id=a,source=source,raster=raster,crop=[0,0,1,1],masks=[],mode='all',question='',answer='',version=1,published_version=0)
  self.put('authoring',a,draft);return draft
 def draft(self,a):
  result=self.get('authoring',identifier(a))
  if not result:raise ValueError('遮罩工程不存在')
  return result
 def save(self,a,value,*,expected):
  with self.db() as db:
   old=self.get('authoring',identifier(a),db)
   if not old or old['version']!=expected:raise Conflict('遮罩草稿版本冲突；本地草稿已保留')
   if any(x['authoring_id']==a and x['status']!='complete' for x in self.all('authoring-operations',db)):raise Conflict('先恢复未完成的发布，再修改草稿')
   crop=rectangle(value['crop']);masks=value['masks'];seen=set()
   if len(masks)>100:raise ValueError('遮罩最多 100 个')
   for mask in masks:
    identifier(mask['mask_id']);rectangle(mask['rect'])
    if mask['mask_id'] in seen or not contains(crop,mask['rect']):raise ValueError('遮罩重复或裁剪排除了现有遮罩；请先调整')
    seen.add(mask['mask_id'])
   if value['mode'] not in ('all','current'):raise ValueError('未知遮罩模式')
   if any(not isinstance(value.get(k,''),str) or len(value.get(k,''))>50000 for k in ['question','answer']):raise ValueError('说明过长')
   result=dict(old,**{k:value[k] for k in ['crop','masks','mode','question','answer']},version=expected+1)
   self.put('authoring',a,result,db);return result
 def stage(self,a,version,request_id,confirmed_deletions):
  if not request_id or len(request_id)>128:raise ValueError('缺少发布请求身份')
  fingerprint=digest(canonical([a,version,sorted(confirmed_deletions)]).encode());opid=digest(request_id.encode())
  with self.db() as db:
   op=self.get('authoring-operations',opid,db)
   if op:
    if op['fingerprint']!=fingerprint:raise Conflict('请求身份已用于其他发布')
    return op
   rejected=self.get('authoring-rejections',opid,db)
   if rejected:
    if rejected['fingerprint']!=fingerprint:raise Conflict('请求身份已用于其他发布')
    raise Conflict('该发布请求已被拒绝，请确认最新草稿后使用新请求')
   error=None
   try:
    draft=self.get('authoring',identifier(a),db)
    if draft['version']!=version:raise Conflict('草稿版本变化，请重新预览')
    if any(x['authoring_id']==a and x['status']!='complete' for x in self.all('authoring-operations',db)):raise Conflict('已有待恢复的发布')
    links=[x for x in self.all('occlusion-links',db) if x['authoring_id']==a and not x.get('deleted')]
    if not draft['masks'] and not links:raise ValueError('至少需要一个遮罩或已发布的待删除笔记')
    maskids={x['mask_id'] for x in draft['masks']};removed=[x for x in links if x['mask_id'] not in maskids]
    if {x['note_id'] for x in removed}!=set(confirmed_deletions):raise Conflict('删除必须逐项确认准确的 Anki 笔记身份')
    op=dict(operation_id=opid,authoring_id=a,version=version,fingerprint=fingerprint,status='pending',snapshot=draft,items=[dict(mask_id=m['mask_id'],action='save',status='pending') for m in draft['masks']]+[dict(mask_id=m['mask_id'],action='delete',status='pending',note_id=m['note_id']) for m in removed])
    self.put('authoring-operations',opid,op,db);return op
   except (Conflict,ValueError) as exc:
    # Commit a durable no-operation outcome BEFORE returning the rejection.
    # This request identity can never stage later after an ambiguous retry.
    self.put('authoring-rejections',opid,dict(operation_id=opid,authoring_id=a,fingerprint=fingerprint,state='rejected',message=str(exc)),db)
    error=exc
  if error:raise error
 def request_status(self,a,version,request_id,confirmed_deletions):
  fingerprint=digest(canonical([a,version,sorted(confirmed_deletions)]).encode());opid=digest(request_id.encode())
  with self.db() as db:
   op=self.get('authoring-operations',opid,db);rejected=self.get('authoring-rejections',opid,db)
   known=op or rejected
   if known and (known['authoring_id']!=a or known['fingerprint']!=fingerprint):raise Conflict('请求身份已用于其他发布')
   if op:return dict(state='operation',operation=op)
   if rejected:return dict(state='rejected',rejection=rejected)
   return dict(state='unknown')
 def operation(self,op):
  result=self.get('authoring-operations',identifier(op))
  if not result:raise ValueError('发布操作不存在')
  return result
 async def publish(self,opid,owner,*,deck,suspended,record_source,fault=lambda phase:None):
  op=self.operation(opid)
  if op['status']=='complete':return op
  for index,item in enumerate(op['items']):
   if item['status']=='complete':continue
   key=op['authoring_id']+':'+item['mask_id'];link=self.get('occlusion-links',key)
   try:
    if item['action']=='delete':
     result=await owner('lectureManagedDelete',dict(key=key,note_id=item['note_id'],expected_hash=link['fields_hash']))
     self.put('occlusion-links',key,dict(link,deleted=True))
    else:
     import asyncio
     images=await asyncio.to_thread(compose,op['snapshot'],item['mask_id'])
     names=[]
     for data in images:
      name=await owner('storeMediaFile',dict(filename='ir4anki-'+digest(data)+'.png',data=base64.b64encode(data).decode()))
      names.append(name)
     fault('media')
     fields={'Question':safe_text(op['snapshot']['question'])+f'<img src="{html.escape(names[0],quote=True)}">','Answer':safe_text(op['snapshot']['answer'])+f'<img src="{html.escape(names[1],quote=True)}">','AuthoringKey':key}
     result=await owner('lectureManagedNote',dict(key=key,fields=fields,expected_hash=(link or {}).get('fields_hash'),deck=deck,suspended=suspended))
     fault('note')
     # Persist each owner result immediately. Retry looks up immutable key and
     # verifies either old baseline or exact desired fields before mutation.
     self.put('occlusion-links',key,dict(result,authoring_id=op['authoring_id'],mask_id=item['mask_id'],media=names,deleted=False))
     await record_source(result['note_id'],op['snapshot']['source'])
    item.update(status='complete',result=result,error=None)
   except Exception as exc:
    item.update(status='failed',error=str(exc)[:1500]);op['status']='partial';self.put('authoring-operations',opid,op);return op
   self.put('authoring-operations',opid,op);fault('item')
  op['status']='complete';self.put('authoring-operations',opid,op)
  with self.db() as db:
   draft=self.get('authoring',op['authoring_id'],db);draft['published_version']=op['version'];self.put('authoring',op['authoring_id'],draft,db)
  return op
