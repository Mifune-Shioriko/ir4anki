"""Immutable original/PDF revisions, recoverable local conversion jobs."""
import io
import re
import uuid
import zipfile
from pathlib import Path,PurePosixPath
from xml.etree import ElementTree
from lecture_store import Store,Conflict,atomic,digest,identifier
from document_worker import invoke
MAX_BYTES=64*1024**2
# MS-PPT 2.3.1 / 2.13.24: native record headers and IDs, not OLE directory names.
# https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-ppt/38fb1fa5-0a62-477a-8b14-178df22de812
PPT_MAX_RECORDS=250_000
PPT_MAX_DEPTH=64

def _validate_ppt_records(ole):
 """Bounded structural rejection before any job/snapshot/converter is created.

 All native object storage variants share RT_ExternalOleObjectStg, including
 compressed VBA/ActiveX. Reject the record header without inflating its body.
 Scan obsolete edits too: an importer must not choose a different unsafe edit.
 This is a format policy, not filesystem/network sandboxing of LibreOffice.
 """
 import struct
 def read(name,limit):
  if ole.get_size(name)>limit:raise ValueError('PPT 原生记录资源超限')
  with ole.openstream(name) as stream:data=stream.read(limit+1)
  if len(data)>limit:raise ValueError('PPT 原生记录资源超限')
  return data
 current=read('Current User',1024**2)
 if len(current)<28:raise ValueError('损坏 PPT Current User 记录')
 vi,kind,size=struct.unpack_from('<HHI',current)
 if vi!=0 or kind!=0x0FF6 or size!=len(current)-8 or struct.unpack_from('<I',current,8)[0]!=20:raise ValueError('损坏 PPT Current User 记录')
 # MS-PPT 2.3.2 / 2.3.7: the encrypted header remains readable.
 if struct.unpack_from('<I',current,12)[0]!=0xE391C05F:raise ValueError('不允许加密 PPT，请先导出 PDF')
 data=read('PowerPoint Document',MAX_BYTES)
 if not data:raise ValueError('空 PPT 原生记录流')
 forbidden={0x1011,0x0FC3,0x0FCC,0x0FCD,0x0FCE,0x0FD1,0x0FEE,0x0FFB,0x2F14}
 containers={0x03E8,0x03EE,0x03F0,0x03F2,0x03F8,0x03FF,0x0FC9,0x138A}
 stack=[(0,len(data),0,None)];count=0
 while stack:
  pos,end,depth,parent=stack.pop();tag=None
  while pos<end:
   count+=1
   if count>PPT_MAX_RECORDS or depth>PPT_MAX_DEPTH:raise ValueError('PPT 原生记录数量或嵌套超限')
   if end-pos<8:raise ValueError('损坏 PPT 原生记录头')
   vi,kind,size=struct.unpack_from('<HHI',data,pos);body=pos+8;stop=body+size
   if stop>end:raise ValueError('PPT 原生记录长度越界')
   if kind in forbidden:raise ValueError('不允许宏、加密或嵌入/链接活动对象 PPT，请先导出 PDF')
   version=vi&15
   if kind in containers and version!=15:raise ValueError('损坏 PPT 容器记录')
   if kind==0x0400:  # VBAInfoAtom: retain the common empty/no-project case.
    if vi!=2 or size!=12:raise ValueError('损坏 PPT VBA 信息记录')
    reference,has_macros,_runtime=struct.unpack_from('<III',data,body)
    if reference or has_macros:raise ValueError('不允许宏 PPT，请先导出 PDF')
   if kind==0x0FF5:
    if vi!=0 or size not in (28,32):raise ValueError('损坏 PPT UserEdit 记录')
    if size==32 and struct.unpack_from('<I',data,body+28)[0]:raise ValueError('不允许加密 PPT，请先导出 PDF')
   # MS-PPT 3.6.2: known ___PPTn extension data contains records even though
   # RT_BinaryTagDataBlob uses recVer=0. Unknown opaque tag data stays opaque.
   if parent==0x138A and kind==0x0FBA:
    tag=data[body:stop].decode('utf-16-le',errors='replace') if size<=32 else None
   extension=parent==0x138A and kind==0x138B and tag in ('___PPT9','___PPT10','___PPT11','___PPT12')
   if version==15 or extension:
    if depth>=PPT_MAX_DEPTH:raise ValueError('PPT 原生记录嵌套超限')
    stack.append((body,stop,depth+1,kind))
   pos=stop

# Passive webpage links are document data; only cached image loading references
# need a conversion-copy edit. The uploaded immutable original is never rewritten.
_PKG_REL='http://schemas.openxmlformats.org/package/2006/relationships'
_OFFICE_RELS=('http://schemas.openxmlformats.org/officeDocument/2006/relationships','http://purl.oclc.org/ooxml/officeDocument/relationships')
_DRAWING=('http://schemas.openxmlformats.org/drawingml/2006/main','http://purl.oclc.org/ooxml/drawingml/main')

def _pptx_xml(data):
 if len(data)>8*1024**2:raise ValueError('PPTX 单个 XML 超出资源限制')
 class NoDoctype(ElementTree.TreeBuilder):
  def doctype(self,*args):raise ValueError('不允许 PPTX XML 实体')
 return ElementTree.fromstring(data,parser=ElementTree.XMLParser(target=NoDoctype()))

def _pptx_relationship_plan(z,name,xml):
 import posixpath
 from urllib.parse import urlsplit,unquote
 tree=_pptx_xml(xml)
 if tree.tag!='{'+_PKG_REL+'}Relationships' or len(tree)>20000:raise ValueError('PPTX 关系命名空间或数量无效')
 rels={};external={}
 for node in tree:
  if node.tag!='{'+_PKG_REL+'}Relationship':raise ValueError('PPTX 关系命名空间无效')
  rid,kind,target=node.get('Id'),node.get('Type'),node.get('Target');mode=node.get('TargetMode','Internal')
  if not rid or rid in rels or not kind or not target or mode not in ('Internal','External'):raise ValueError('PPTX 关系 ID、类型或目标无效/重复')
  rels[rid]=node
  if mode=='External':external[rid]=node
  elif urlsplit(target).scheme or urlsplit(target).netloc or '\\' in target:raise ValueError('PPTX 本地资源关系不能指向外部位置')
 if not external:return None,set()
 part=PurePosixPath(name)
 owner=None if name=='_rels/.rels' else str(part.parent.parent/part.name[:-5]) if part.parent.name=='_rels' and part.name.endswith('.rels') else None
 owner_tree=_pptx_xml(z.read(owner)) if owner and owner in z.namelist() else None
 references={rid:[] for rid in external}
 if owner_tree is not None:
  for element in owner_tree.iter():
   for attr,value in element.attrib.items():
    if value in external and any(attr.startswith('{'+ns+'}') for ns in _OFFICE_RELS):references[value].append((element,attr))
 remove=set()
 for rid,node in external.items():
  kind,target=node.get('Type'),node.get('Target')
  if kind in {ns+'/hyperlink' for ns in _OFFICE_RELS}:
   try:
    uri=urlsplit(target);port=uri.port
    valid=uri.scheme in ('http','https') and bool(uri.hostname) and not uri.username and not uri.password and not any(c.isspace() or ord(c)<32 or ord(c)==127 for c in target) and '\\' not in target and not any(ord(c)<32 or ord(c)==127 for c in unquote(target))
   except ValueError:valid=False
   if not valid:raise ValueError('网页超链接地址无效；仅支持 HTTP/HTTPS 地址')
   for element,attr in references[rid]:
    if element.tag not in { '{'+ns+'}'+tag for ns in _DRAWING for tag in ('hlinkClick','hlinkMouseOver')} or attr not in {'{'+ns+'}id' for ns in _OFFICE_RELS}:raise ValueError('PPTX 超链接被用作外部加载资源')
   continue  # Preserve original hyperlink target, relationship, text and action.
  if kind not in {ns+'/image' for ns in _OFFICE_RELS}:raise ValueError('课件需要未支持的外部媒体/对象资源；请将资源嵌入课件后重试')
  for element,attr in references[rid]:
   if element.tag not in {'{'+ns+'}blip' for ns in _DRAWING} or attr not in {'{'+ns+'}link' for ns in _OFFICE_RELS}:raise ValueError('PPTX 外部图片引用无效')
   embed=next((element.get('{'+ns+'}embed') for ns in _OFFICE_RELS if element.get('{'+ns+'}embed')),None);cached=rels.get(embed)
   if cached is None or cached.get('Type') not in {ns+'/image' for ns in _OFFICE_RELS} or cached.get('TargetMode','Internal')!='Internal':raise ValueError('课件外部图片没有嵌入缓存；请将图片嵌入课件后重试')
   target=cached.get('Target');uri=urlsplit(target)
   path=unquote(uri.path)
   local=posixpath.normpath(path.lstrip('/') if path.startswith('/') else posixpath.join(posixpath.dirname(owner),path))
   if uri.scheme or uri.netloc or '\\' in path or local.startswith('../') or local not in z.namelist():raise ValueError('课件嵌入图片缓存缺失或路径无效')
   from PIL import Image
   try:
    with Image.open(io.BytesIO(z.read(local))) as image:
     if image.width*image.height>24_000_000:raise ValueError('图片缓存像素超限')
     image.verify()
   except (OSError,SyntaxError,Image.DecompressionBombError) as exc:raise ValueError('课件嵌入图片缓存损坏或格式不支持') from exc
  # Unused image relationships require no content and can safely be removed.
  remove.add(rid)
 return owner,remove

def _pptx_conversion_copy(data):
 """Return an offline copy only when image reload references need removal."""
 from xml.dom import minidom
 changes={}
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  for item in z.infolist():
   if not item.filename.endswith('.rels'):continue
   xml=z.read(item);owner,remove=_pptx_relationship_plan(z,item.filename,xml)
   if not remove:continue
   # DOM keeps namespace declarations and QName-valued attributes intact.
   rel_doc=minidom.parseString(xml)
   for node in list(rel_doc.documentElement.childNodes):
    if node.nodeType==node.ELEMENT_NODE and node.namespaceURI==_PKG_REL and node.getAttribute('Id') in remove:rel_doc.documentElement.removeChild(node)
   changes[item.filename]=rel_doc.toxml(encoding='utf-8');rel_doc.unlink()
   if owner:
    doc=minidom.parseString(z.read(owner))
    for ns in _DRAWING:
     for blip in doc.getElementsByTagNameNS(ns,'blip'):
      for rel_ns in _OFFICE_RELS:
       if blip.getAttributeNS(rel_ns,'link') in remove:blip.removeAttributeNS(rel_ns,'link')
    changes[owner]=doc.toxml(encoding='utf-8');doc.unlink()
  if not changes:return None
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w') as dest:
   for item in z.infolist():dest.writestr(item,changes.get(item.filename,z.read(item)))
  return out.getvalue()

class Documents(Store):
 def __init__(self,root,*,render_scale=None):
  import os,math
  self.render_scale=float(render_scale if render_scale is not None else os.environ.get('ANKI_LECTURE_RENDER_SCALE','2'))
  if not math.isfinite(self.render_scale) or not .5<=self.render_scale<=4:raise ValueError('ANKI_LECTURE_RENDER_SCALE 必须介于 0.5 和 4')
  super().__init__(root)
 def _revision_path(self,d,r):return self.root/'documents'/identifier(d)/identifier(r)
 def document(self,d):
  result=self.get('documents',identifier(d))
  if not result:raise ValueError('课件不存在')
  return result
 def revision(self,d,r):
  result=self.get('revisions',identifier(r))
  if not result or result['document_id']!=identifier(d):raise ValueError('课件版本不存在')
  return result
 def original_path(self,d,r):
  rev=self.revision(d,r);return self._revision_path(d,r)/('original.'+rev['format'])
 def pdf_path(self,d,r):
  rev=self.revision(d,r)
  if rev['status']!='ready':raise Conflict('课件正在转换或转换失败')
  return self._revision_path(d,r)/'reading.pdf'
 def list(self):return self.all('documents')
 def import_bytes(self,path,data,*,document_id=None,inspect=None,format_override=None):
  if not path or path.startswith(('/', '\\')) or '\\' in path or any(x in ('..','.','') or x.startswith('.') for x in path.split('/')):raise ValueError('非法课件路径')
  ext=format_override or PurePosixPath(path).suffix.lower().lstrip('.')
  if ext not in ('pdf','ppt','pptx'):raise ValueError('仅支持 PDF/PPT/PPTX')
  if not 0<len(data)<=MAX_BYTES:raise ValueError('课件大小超出限制')
  if ext=='pdf' and not data.startswith(b'%PDF-'):raise ValueError('不是有效 PDF')
  if ext=='ppt':
   if not data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):raise ValueError('不是有效的二进制 PPT；请使用原始 PPT 文件')
   try:import olefile
   except ImportError as exc:raise ValueError('缺少 PPT 容器依赖 olefile，请安装后端 requirements 后重试') from exc
   try:
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
     streams=ole.listdir()
     if not ole.exists('PowerPoint Document') or not ole.exists('Current User'):raise ValueError('不是 PowerPoint 二进制课件')
     if any(any(part.lower() in ('encryptedpackage','encryptioninfo','vba','_vba_project_cur','macros') for part in entry) for entry in streams):raise ValueError('不允许加密或宏 PPT，请先导出 PDF')
     if any(any(part.lower() in ('objectpool','exobjlist') for part in entry) for entry in streams):raise ValueError('不允许嵌入活动对象，请先导出 PDF')
     _validate_ppt_records(ole)
   except OSError as exc:raise ValueError('损坏 PPT') from exc
  if ext=='pptx':
   try:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
     if len(z.infolist())>20000 or sum(x.file_size for x in z.infolist())>256*1024**2:raise ValueError('PPTX 解压资源超限')
     if len(set(z.namelist()))!=len(z.namelist()):raise ValueError('PPTX 包含重复文件条目')
     if 'ppt/presentation.xml' not in z.namelist():raise ValueError('PPTX 缺少 presentation')
     for item in z.infolist():
      if item.filename.lower().endswith(('.xml','.rels','.svg')) and item.file_size>8*1024**2:raise ValueError('PPTX 单个 XML/SVG 超出资源限制')
      if item.flag_bits & 1 or item.filename.startswith('/') or '..' in item.filename.split('/') or '\\' in item.filename:raise ValueError('不安全或加密的 PPTX 容器')
      if '/embeddings/' in item.filename or '/activeX/' in item.filename:raise ValueError('不允许嵌入活动对象，请先导出 PDF')
      if item.filename.endswith('.xml'):
       xml=z.read(item)
       if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError('不允许 XML 实体')
      if item.filename.lower().endswith('.svg'):
       xml=z.read(item)
       if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError('不允许 SVG 外部实体')
       svg=ElementTree.fromstring(xml)
       for node in svg.iter():
        if node.tag.rsplit('}',1)[-1] in ('script','foreignObject'):raise ValueError('不允许活动 SVG 内容')
        for name,value in node.attrib.items():
         if name.rsplit('}',1)[-1].lower() in ('href','src') and not value.startswith('#'):raise ValueError('不允许 SVG 外部资源，请先导出 PDF')
         if name.rsplit('}',1)[-1].lower().startswith('on') or '@import' in value.lower() or any(not u.strip(' \"\'').startswith('#') for u in re.findall(r'url\(([^)]*)\)',value,re.I)):raise ValueError('不允许 SVG 外部样式资源')
        if node.text and 'url(' in node.text.lower():raise ValueError('不允许 SVG 外部样式资源')
      if 'vbaProject' in item.filename:raise ValueError('不允许宏课件')
      if item.filename.endswith('.rels'):
       xml=z.read(item)
       if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError('不允许 XML 实体')
       _pptx_relationship_plan(z,item.filename,xml)
   except (zipfile.BadZipFile,ElementTree.ParseError) as exc:raise ValueError('损坏 PPTX') from exc
  existing=next((x for x in self.list() if x['path']==path),None)
  d=document_id or (existing['document_id'] if existing else uuid.uuid4().hex)
  previous=self.document(d) if document_id else (existing or {})
  if document_id and path!=previous['path']:raise Conflict('新版本必须沿用逻辑路径；改名或移动请使用文件操作')
  if existing and existing['document_id']!=d:raise Conflict('路径已属于其他课件')
  original_hash=digest(data);r=digest((d+original_hash).encode());old=self.get('revisions',r)
  if old:return old
  folder=self._revision_path(d,r);folder.mkdir(parents=True,exist_ok=True);atomic(folder/('original.'+ext),data)
  rev=dict(document_id=d,revision_id=r,original_hash=original_hash,format=ext,status='ready' if ext=='pdf' else 'waiting')
  if ext=='pdf':
   metadata=inspect(folder/'original.pdf') if inspect else invoke('inspect',{'path':str(folder/'original.pdf')},self.root/'worker')
   atomic(folder/'reading.pdf',data);rev.update(metadata,pdf_hash=original_hash)
  else:rev['job_id']=uuid.uuid4().hex
  with self.db() as db:
   self.put('revisions',r,rev,db)
   self.put('documents',d,dict(previous,document_id=d,path=path,title=PurePosixPath(path).name,current_revision=r,listed=bool(previous.get('listed'))),db)
   if ext!='pdf':self.put('jobs',rev['job_id'],dict(job_id=rev['job_id'],document_id=d,revision_id=r,status='waiting',attempts=0),db)
  return rev
 def job(self,j):
  result=self.get('jobs',identifier(j))
  if not result:raise ValueError('转换任务不存在')
  return result
 def retry(self,j):
  with self.db() as db:
   job=self.get('jobs',identifier(j),db)
   if not job or job['status'] not in ('failed','waiting'):raise Conflict('该任务不能重试')
   job.update(status='waiting',error=None);self.put('jobs',j,job,db)
   rev=self.get('revisions',job['revision_id'],db);rev.update(status='waiting',error=None);self.put('revisions',job['revision_id'],rev,db)
  return job
 def recover_jobs(self):
  for job in self.all('jobs'):
   if job['status']=='running':
    job.update(status='waiting',error='进程中断，等待恢复');self.put('jobs',job['job_id'],job)
    rev=self.revision(job['document_id'],job['revision_id']);rev.update(status='waiting');self.put('revisions',job['revision_id'],rev)
 def run_job(self,j,*,converter=None):
  with self.db() as db:
   job=self.get('jobs',identifier(j),db)
   if job['status']!='waiting':return job
   job.update(status='running',attempts=job['attempts']+1);self.put('jobs',j,job,db)
   rev=self.get('revisions',job['revision_id'],db);rev.update(status='running');self.put('revisions',job['revision_id'],rev,db)
  d,r=job['document_id'],job['revision_id'];folder=self._revision_path(d,r)
  try:
   tmp=folder/'conversion.pending.pdf'
   source=self.original_path(d,r)
   if rev['format']=='pptx':
    offline=_pptx_conversion_copy(source.read_bytes())
    if offline is not None:
     source=folder/'conversion-input.pptx';atomic(source,offline)
   metadata=(converter or (lambda src,out:invoke('convert',dict(path=str(src),out=str(out),scratch=str(self.root/'worker')),self.root/'worker')))(source,tmp)
   pdfmeta=invoke('inspect',{'path':str(tmp)},self.root/'worker');pdf=tmp.read_bytes();atomic(folder/'reading.pdf',pdf)
   with self.db() as db:
    rev=self.get('revisions',r,db);rev.update(pdfmeta,status='ready',pdf_hash=digest(pdf),conversion=metadata);self.put('revisions',r,rev,db)
    job.update(status='ready',error=None);self.put('jobs',j,job,db)
  except Exception as exc:
   job.update(status='failed',error=str(exc)[:1500])
   with self.db() as db:
    self.put('jobs',j,job,db)
    rev=self.get('revisions',r,db);rev.update(status='failed',error=job['error']);self.put('revisions',r,rev,db)
  return job
 def raster(self,d,r,page):
  rev=self.revision(d,r)
  if not isinstance(page,int) or not 1<=page<=rev.get('page_count',0):raise ValueError('页码越界')
  from document_worker import render_profile
  PROFILE=render_profile(self.render_scale)
  folder=self._revision_path(d,r)/'rasters'/PROFILE;out=folder/f'{page}.png';meta=self.get('rasters',f'{r}/{page}/{PROFILE}')
  if meta and out.exists() and digest(out.read_bytes())==meta['raster_hash']:return meta
  folder.mkdir(parents=True,exist_ok=True);tmp=folder/f'{page}.pending.png'
  metadata=invoke('render',dict(path=str(self.pdf_path(d,r)),page=page,out=str(tmp),scale=self.render_scale),self.root/'worker')
  image=tmp.read_bytes();h=digest(image);target=folder/(h+'.png');atomic(target,image);atomic(out,image)
  meta=dict(metadata,raster_hash=h,path=str(target),document_id=d,revision_id=r,page=page)
  self.put('rasters',f'{r}/{page}/{PROFILE}',meta);return meta
 def raster_by_hash(self,d,r,page,hash):
  identifier(d);identifier(r);identifier(hash)
  metadata=next((x for x in self.all('rasters') if x['document_id']==d and x['revision_id']==r and x['page']==page and x['raster_hash']==hash),None)
  if not metadata:raise Conflict('找不到此不可变基准图，请保留草稿检查备份')
  path=Path(metadata['path'])
  if not path.is_relative_to(self._revision_path(d,r)) or not path.exists() or digest(path.read_bytes())!=hash:raise Conflict('基准图损坏或被改动，不能继续作者操作')
  return metadata
 def thumbnail(self,d,r,page):
  rev=self.revision(d,r)
  if not isinstance(page,int) or isinstance(page,bool) or not 1<=page<=rev.get('page_count',0):raise ValueError('页码越界')
  from document_worker import PROFILE
  folder=self._revision_path(d,r)/'thumbnails'/PROFILE;out=folder/f'{page}.png'
  if not out.exists():
   folder.mkdir(parents=True,exist_ok=True);tmp=folder/f'{page}.pending.png'
   invoke('thumbnail',dict(path=str(self.pdf_path(d,r)),page=page,out=str(tmp)),self.root/'worker')
   atomic(out,tmp.read_bytes())
  return out
 def relocate(self,old,new,is_dir=False):
  with self.db() as db:
   for doc in self.all('documents',db):
    p=doc['path']
    if p==old or (is_dir and p.startswith(old+'/')):doc['path']=new+p[len(old):];doc['title']=PurePosixPath(doc['path']).name;self.put('documents',doc['document_id'],doc,db)
