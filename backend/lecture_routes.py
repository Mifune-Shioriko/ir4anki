"""Thin same-origin routes; all heavy PDF work runs outside event loop."""
import asyncio
import base64
from pathlib import Path
from fastapi import APIRouter,HTTPException,File,UploadFile,Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from lecture_store import Conflict
from lecture_occlusion import compose,crop_image

class Split(BaseModel):
 segment_id:str
 revision_id:str
 page_start:int
 page_end:int
 expected_version:int
 request_id:str
class AnnotationBody(BaseModel):
 body:str
 expected_version:str
 request_id:str
class DraftBody(BaseModel):
 value:dict
 expected_version:int
class PublishBody(BaseModel):
 version:int
 request_id:str
 confirmed_deletions:list[int]=[]
class CropBody(BaseModel):
 rect:list[float]
 raster_hash:str
class CreateBody(BaseModel):
 source:dict

async def jobs(h):
 h._lectures().docs.recover_jobs()
 while True:
  waiting=[j for j in h._lectures().docs.all('jobs') if j['status']=='waiting']
  if waiting:
   async with h._lecture_heavy:await asyncio.to_thread(h._lectures().docs.run_job,waiting[0]['job_id'])
  else:await asyncio.sleep(1)

def install(h):
 from fastapi.routing import APIRoute
 class LectureRoute(APIRoute):
  def get_route_handler(self):
   fn=super().get_route_handler()
   async def handler(request):
    try:return await fn(request)
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except (ValueError,KeyError) as exc:raise HTTPException(400,str(exc)) from exc
    except OSError as exc:raise HTTPException(503,'文档 I/O 失败，内容已保留，请重试') from exc
   return handler
 router=APIRouter(prefix='/api/lectures',route_class=LectureRoute)
 def L():return h._lectures()
 def validate_page(d,r,page):
  rev=L().docs.revision(d,r)
  if not 1<=page<=rev.get('page_count',0):raise ValueError('页码越界或课件尚未可读')
 def source(src):
  validate_page(src['document_id'],src['revision_id'],src['page']);seg=L().reading.segment(src['segment_id'])
  L().effective_source(src)
 async def heavy(fn,*args):
  async with h._lecture_heavy:return await asyncio.to_thread(fn,*args)
 @router.get('')
 async def listing():
  return {'documents':[dict(d,revision=L().docs.revision(d['document_id'],d['current_revision']),revisions=[x for x in L().docs.all('revisions') if x['document_id']==d['document_id']]) for d in L().docs.list() if not d.get('original_missing')] }
 @router.post('/import')
 async def importing(file:UploadFile=File(...),path:str=Form(...),document_id:str|None=Form(None)):
  data=await h._read_upload_bounded(file,64*1024**2,'课件')
  async with h._state_lock:
   target=h._files_resolve(path)
   # The managed immutable snapshot is made first; normal corpus original is
   # written only after validation. Existing bytes require a new explicit revision.
   if target.exists() and document_id is None:raise Conflict('文件已存在，请明确选择新增版本')
   rev=await heavy(lambda:L().docs.import_bytes(path,data,document_id=document_id))
   from lecture_store import atomic
   await asyncio.to_thread(atomic,target,data)
   return rev
 @router.get('/{d}/annotations/search')
 async def search_annotations(d:str,q:str):
  L().docs.document(d)
  return {'matches':await heavy(L().annotations.search,d,q)}
 @router.post('/{d}/pdf-revision')
 async def pdf_revision(d:str,file:UploadFile=File(...)):
  data=await h._read_upload_bounded(file,64*1024**2,'PDF 阅读版本')
  async with h._state_lock:
   doc=L().docs.document(d)
   # Keep the logical original (including PPTX) and all existing reading
   # references; this is an explicit new immutable PDF representation only.
   return await heavy(lambda:L().docs.import_bytes(doc['path'],data,document_id=d,format_override='pdf'))
 @router.get('/{d}/revisions/{r}')
 async def revision(d:str,r:str):return L().docs.revision(d,r)
 @router.get('/jobs/{j}')
 async def job(j:str):return L().docs.job(j)
 @router.post('/jobs/{j}/retry')
 async def retry(j:str):return L().docs.retry(j)
 @router.post('/{d}/revisions/{r}/read')
 async def add(d:str,r:str):
  async with h._state_lock:
   rev=L().docs.revision(d,r);doc=L().docs.document(d)
   if rev['status']!='ready':raise Conflict('等待转换完成')
   if doc.get('listed') and doc.get('reading_revision')!=r:raise Conflict('旧版本仍在阅读清单，请先显式移除；进度不会自动迁移')
   seg=L().reading.seed(d,r,doc['path'],rev['page_count']);doc.update(listed=True,reading_revision=r,position=len(h._reading_read()['list']));L().docs.put('documents',d,doc)
   return L().reading.payload(seg)
 @router.get('/{d}/revisions/{r}/pdf')
 async def pdf(d:str,r:str):return FileResponse(L().docs.pdf_path(d,r),media_type='application/pdf')
 @router.get('/{d}/revisions/{r}/pages/{page}/thumbnail')
 async def thumbnail(d:str,r:str,page:int):return FileResponse(await heavy(L().docs.thumbnail,d,r,page),media_type='image/png')
 @router.get('/{d}/revisions/{r}/pages/{page}/raster')
 async def raster(d:str,r:str,page:int):
  meta=await heavy(L().docs.raster,d,r,page)
  return dict(meta,url=f'/api/lectures/{d}/revisions/{r}/pages/{page}/image?hash={meta["raster_hash"]}')
 @router.get('/{d}/revisions/{r}/pages/{page}/image')
 async def image(d:str,r:str,page:int,hash:str):
  meta=await heavy(L().docs.raster_by_hash,d,r,page,hash)
  return FileResponse(meta['path'],media_type='image/png')
 @router.post('/{d}/revisions/{r}/pages/{page}/crop')
 async def crop(d:str,r:str,page:int,body:CropBody):
  if h._pylib is None:raise HTTPException(409,'截图媒体需要原生 owner')
  meta=await heavy(L().docs.raster,d,r,page)
  if meta['raster_hash']!=body.raster_hash:raise Conflict('页面基准图变化，请重新选择截图')
  data=await heavy(crop_image,meta,body.rect)
  from lecture_store import digest
  name=await h.anki('storeMediaFile',dict(filename='ir4anki-crop-'+digest(data)+'.png',data=base64.b64encode(data).decode()))
  return dict(filename=name,raster_hash=meta['raster_hash'],rect=body.rect)
 @router.get('/{d}/revisions/{r}/pages/{page}/annotation')
 async def annotation(d:str,r:str,page:int):
  validate_page(d,r,page);return await asyncio.to_thread(L().annotations.read,d,r,page)
 @router.put('/{d}/revisions/{r}/pages/{page}/annotation')
 async def save_annotation(d:str,r:str,page:int,body:AnnotationBody):
  validate_page(d,r,page)
  async with h._state_lock:return await heavy(lambda:L().annotations.save(d,r,page,body=body.body,expected=body.expected_version,request_id=body.request_id))
 @router.get('/{d}/revisions/{r}/pages/{page}/reading-source')
 async def page_reading_source(d:str,r:str,page:int,segment_id:str):
  validate_page(d,r,page)
  effective=L().effective_source(dict(kind='pdf',document_id=d,revision_id=r,page=page,segment_id=segment_id))
  seg=L().reading.segment(effective['segment_id']);doc=L().docs.document(d)
  return dict(L().reading.payload(dict(seg,path=doc['path'])),page=page)
 @router.get('/{d}/revisions/{r}/pages/{page}/related')
 async def related(d:str,r:str,page:int):
  validate_page(d,r,page)
  return dict(note_ids=L().page_notes(d,r,page),document_id=d,revision_id=r,page=page)
 @router.post('/split')
 async def split(body:Split):
  async with h._state_lock:
   seg=L().reading.segment(body.segment_id)
   if seg['revision_id']!=body.revision_id:raise Conflict('课件版本变化')
   result=L().reading.split(body.segment_id,body.page_start,body.page_end,expected=body.expected_version,request_id=body.request_id)
   data=h._reading_read();h._reading_write(data)
   flow=h._flow_read()
   if flow:L().repair_flow(flow);h._flow_write(flow)
   return dict(result,chunks=[L().reading.payload(c) for c in result['replacements']])
 @router.post('/authoring')
 async def create(body:CreateBody):
  source(body.source);s=body.source
  raster=await heavy(L().docs.raster,s['document_id'],s['revision_id'],s['page'])
  async with h._state_lock:return L().authoring.create(s,raster)
 def authoring_view(a):
  value=L().authoring.draft(a)
  pending=next((x for x in L().authoring.all('authoring-operations') if x['authoring_id']==a and x['status']!='complete'),None)
  links=[x for x in L().authoring.all('occlusion-links') if x['authoring_id']==a and not x.get('deleted')]
  try:effective=L().effective_source(value['source']);ownership_error=None
  except (ValueError,Conflict) as exc:effective=None;ownership_error=str(exc)
  return dict(value,effective_source=effective,ownership_error=ownership_error,pending_operation=pending,note_links=links)
 @router.get('/{d}/revisions/{r}/pages/{page}/authoring')
 async def page_authoring(d:str,r:str,page:int):
  validate_page(d,r,page)
  # Physical page identity survives bookmark splitting; never key by segment.
  async with h._state_lock:
   ids=sorted(x['authoring_id'] for x in L().authoring.all('authoring') if all(x['source'].get(k)==v for k,v in [('document_id',d),('revision_id',r),('page',page)]))
   return dict(drafts=[authoring_view(a) for a in ids])
 @router.get('/authoring/{a}')
 async def draft(a:str):return authoring_view(a)
 @router.post('/authoring/{a}/publication-status')
 async def publication_status(a:str,body:PublishBody):
  async with h._state_lock:
   return dict(L().authoring.request_status(a,body.version,body.request_id,body.confirmed_deletions),draft=authoring_view(a))
 @router.put('/authoring/{a}')
 async def save_draft(a:str,body:DraftBody):
  async with h._state_lock:return L().authoring.save(a,body.value,expected=body.expected_version)
 @router.post('/authoring/{a}/preview/{mask}')
 async def preview(a:str,mask:str):
  draft=L().authoring.draft(a)
  if mask not in {m['mask_id'] for m in draft['masks']}:raise ValueError('遮罩不存在')
  images=await heavy(compose,draft,mask)
  return {'images':['data:image/png;base64,'+base64.b64encode(v).decode() for v in images]}
 @router.post('/authoring/{a}/publish')
 async def publish(a:str,body:PublishBody):
  if h._pylib is None:raise HTTPException(409,'受管图片制卡需要原生 owner，Connect 模式暂不可发布')
  async with h._state_lock:
   L().effective_source(L().authoring.draft(a)['source'])
   op=L().authoring.stage(a,body.version,body.request_id,body.confirmed_deletions)
   async def record(n,s):L().record(n,s)
   return await L().authoring.publish(op['operation_id'],h.anki,deck=h.PREVIEW_DECK if h.PREVIEW_MODE else h.RELEASE_DECK,suspended=h.PREVIEW_MODE,record_source=record)
 @router.post('/authoring/{a}/resume/{operation}')
 async def resume_publish(a:str,operation:str):
  if h._pylib is None:raise HTTPException(409,'受管图片制卡需要原生 owner')
  async with h._state_lock:
   op=L().authoring.operation(operation)
   if op['authoring_id']!=a:raise HTTPException(409,'发布操作与草稿不匹配')
   L().effective_source(op['snapshot']['source'])
   async def record(n,s):L().record(n,s)
   return await L().authoring.publish(operation,h.anki,deck=h.PREVIEW_DECK if h.PREVIEW_MODE else h.RELEASE_DECK,suspended=h.PREVIEW_MODE,record_source=record)
 @router.get('/operations/{operation}')
 async def operation(operation:str):return L().authoring.operation(operation)
 h.app.include_router(router)
