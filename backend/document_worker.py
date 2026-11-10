"""Bounded process boundary for untrusted PDF parsing and LibreOffice.

No collection, credentials, network client or application import in this worker.
"""
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile

MAX_PIXELS=24_000_000
MAX_PAGES=2000
from importlib.metadata import version,PackageNotFoundError
def _version(package):
 try:return version(package)
 except PackageNotFoundError:return 'unavailable'
def render_profile(scale=2):return f'pdfium-{_version("pypdfium2")}-pillow-{_version("Pillow")}-rgb-v1-scale{scale:g}-ceil-native-rotation'
PROFILE=render_profile()

def limits():
 resource.setrlimit(resource.RLIMIT_CPU,(90,90))
 resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3))
 resource.setrlimit(resource.RLIMIT_FSIZE,(256*1024**2,256*1024**2))
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))

def inspect_pdf(path):
 import pypdfium2 as pdfium
 import pypdfium2.raw as raw
 doc=None
 try:
  doc=pdfium.PdfDocument(str(path))
  if raw.FPDF_GetSecurityHandlerRevision(doc.raw)>=0:raise ValueError('不支持加密 PDF，请先提供未加密副本')
  if not 1<=len(doc)<=MAX_PAGES:raise ValueError('课件页数超出限制')
  pages=[]
  for i in range(len(doc)):
   page=doc[i]
   try:
    w,h=page.get_size()
    if w<=0 or h<=0 or w*h*4>MAX_PIXELS:raise ValueError('页面像素超出限制')
    pages.append(dict(width=w,height=h,rotation=page.get_rotation(),crop_box=list(page.get_cropbox())))
   finally:page.close()
  return dict(page_count=len(pages),pages=pages)
 except Exception as exc:
  raise ValueError('PDF 损坏、加密或不可读取：'+str(exc)) from exc
 finally:
  if doc:doc.close()

def render_pdf(path,page,out,scale=2):
 import pypdfium2 as pdfium
 import math
 metadata=inspect_pdf(path)
 if not 1<=page<=metadata['page_count']:raise ValueError('页码越界')
 doc=pdfium.PdfDocument(str(path))
 try:
  p=doc[page-1]
  try:
   w,h=p.get_size()
   if not .5<=scale<=4 or w*h*scale*scale>MAX_PIXELS:raise ValueError('渲染规格超出像素限制')
   bitmap=p.render(scale=scale,rev_byteorder=True)
   try:
    img=bitmap.to_pil().convert('RGB')
    if img.width*img.height>MAX_PIXELS:raise ValueError('页面像素超出限制')
    img.save(out,'PNG')
    return dict(width=img.width,height=img.height,render_profile=render_profile(scale))
   finally:bitmap.close()
  finally:p.close()
 finally:doc.close()

def thumbnail_pdf(path,page,out):
 import pypdfium2 as pdfium
 doc=pdfium.PdfDocument(str(path))
 try:
  if not 1<=page<=len(doc):raise ValueError('页码越界')
  p=doc[page-1]
  try:
   w,h=p.get_size();bitmap=p.render(scale=min(192/w,256/h),rev_byteorder=True)
   try:bitmap.to_pil().convert('RGB').save(out,'PNG')
   finally:bitmap.close()
  finally:p.close()
 finally:doc.close()
 return {'profile':PROFILE+'-thumb192'}

def convert_pptx(path,out,scratch):
 exe=shutil.which('libreoffice')
 if not exe:raise RuntimeError('缺少 LibreOffice；原件已保留，可安装后重试或导入 PDF')
 with tempfile.TemporaryDirectory(prefix='lo-',dir=scratch) as tmp:
  tmp=Path(tmp);profile=tmp/'profile';profile.mkdir();(profile/'user').mkdir()
  (profile/'user/registrymodifications.xcu').write_text('''<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Office.Common/Load"><prop oor:name="UpdateDocMode" oor:op="fuse"><value>0</value></prop></item></oor:items>''')
  dest=tmp/'output';dest.mkdir()
  spec='pdf:impress_pdf_Export:'+json.dumps({'ExportHiddenSlides':{'type':'boolean','value':'true'},'ExportNotesPages':{'type':'boolean','value':'false'},'ExportOnlyNotesPages':{'type':'boolean','value':'false'}})
  env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(tmp),'TMPDIR':str(tmp),'LANG':'C.UTF-8','SAL_USE_VCLPLUGIN':'svp'}
  proc=subprocess.run([exe,'-env:UserInstallation='+profile.as_uri(),'--headless','--nologo','--nodefault','--norestore','--convert-to',spec,'--outdir',str(dest),str(path)],env=env,capture_output=True,timeout=75)
  result=dest/(Path(path).stem+'.pdf')
  if proc.returncode or not result.is_file():raise RuntimeError(f'LibreOffice 转换失败（exit {proc.returncode}）：'+proc.stderr.decode(errors='replace')[:800]+proc.stdout.decode(errors='replace')[:400])
  if result.stat().st_size>128*1024**2:raise ValueError('转换 PDF 超出限制')
  shutil.copyfile(result,out)
  font_environment={'available':False}
  if shutil.which('fc-list'):
   from hashlib import sha256
   fonts=subprocess.run(['fc-list','--format','%{family}\n'],env=env,capture_output=True,timeout=5)
   families=sorted(set(fonts.stdout.decode(errors='replace').splitlines()))
   font_environment=dict(available=fonts.returncode==0,family_count=len(families),families_sha256=sha256('\n'.join(families).encode()).hexdigest())
  return dict(converter='LibreOffice',font_environment=font_environment,version=subprocess.run([exe,'--version'],capture_output=True,text=True,timeout=5).stdout.strip(),hidden_slides=True,notes=False,font_warning='字体替换需与可信 PDF 人工比对')

def invoke(action,payload,scratch,timeout=100):
 """Called from an asyncio.to_thread boundary, never on the API event loop."""
 scratch=Path(scratch);scratch.mkdir(parents=True,exist_ok=True)
 env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(scratch),'TMPDIR':str(scratch),'LANG':'C.UTF-8'}
 import signal
 proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),action,json.dumps(payload)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
 try:
  stdout,stderr=proc.communicate(timeout=timeout)
  if proc.returncode:raise ValueError(stderr[-1500:] or '文档处理失败')
  return json.loads(stdout)
 except subprocess.TimeoutExpired as exc:
  raise ValueError('文档处理超时；原件保留，可重试') from exc
 finally:
  # This is only the isolated process group created above. LibreOffice
  # grandchildren must not survive a killed/timed-out worker.
  try:os.killpg(proc.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  proc.wait()

if __name__=='__main__':
 limits();action=sys.argv[1];p=json.loads(sys.argv[2])
 if action=='inspect':result=inspect_pdf(p['path'])
 elif action=='render':result=render_pdf(p['path'],p['page'],p['out'],p.get('scale',2))
 elif action=='thumbnail':result=thumbnail_pdf(p['path'],p['page'],p['out'])
 elif action=='convert':result=convert_pptx(p['path'],p['out'],p['scratch'])
 else:raise ValueError('unknown document job')
 print(json.dumps(result))
