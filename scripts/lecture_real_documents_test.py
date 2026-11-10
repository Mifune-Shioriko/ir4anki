"""Generated non-private fixtures. PPTX output is NOT a trusted lecture reference."""
import io,os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from lecture_documents import Documents
from document_worker import inspect_pdf
from lecture_occlusion import compose

def make_pdf(pages=4):
 import pypdfium2 as pdfium
 doc=pdfium.PdfDocument.new()
 for i in range(pages):
  p=doc.new_page(640 if i%2==0 else 480,480 if i%2==0 else 640);p.gen_content();p.close()
 out=io.BytesIO();doc.save(out);doc.close();return out.getvalue()

def make_pptx():
 from pptx import Presentation
 from pptx.util import Inches
 p=Presentation()
 for i in range(3):
  s=p.slides.add_slide(p.slide_layouts[6]);s.shapes.add_textbox(Inches(1),Inches(1),Inches(7),Inches(1)).text=f'SYNTHETIC LECTURE SLIDE {i+1}'
  s.notes_slide.notes_text_frame.text='PRIVATE_NOTES_SENTINEL_NOT_FOR_EXPORT'
  if i==1:s._element.set('show','0')
 out=io.BytesIO();p.save(out);return out.getvalue()
class RealDocuments(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory(dir=os.environ['NATIVE_FIXTURE_ROOT']);self.d=Documents(self.t.name)
 def tearDown(self):self.t.cleanup()
 def test_actual_pdf_and_raster_opaque_occlusion(self):
  rev=self.d.import_bytes('generated.pdf',make_pdf());self.assertEqual(rev['page_count'],4)
  raster=self.d.raster(rev['document_id'],rev['revision_id'],2)
  self.assertGreater(raster['height'],raster['width'])
  draft=dict(raster=raster,crop=[.1,.1,.8,.8],masks=[dict(mask_id='a'*32,rect=[.2,.2,.2,.2]),dict(mask_id='b'*32,rect=[.6,.6,.1,.1])],mode='all')
  from PIL import Image
  front,back=[Image.open(io.BytesIO(x)).convert('RGB') for x in compose(draft,'a'*32)]
  w,h=raster['width'],raster['height'];x,y=int(.2*w),int(.2*h)
  self.assertEqual(front.getpixel((x,y)),(32,32,32));self.assertEqual(back.getpixel((x,y)),(255,255,255))
  self.assertEqual(front.getpixel((int(.55*w),int(.55*h))),(32,32,32));self.assertEqual(back.getpixel((int(.55*w),int(.55*h))),(32,32,32))
 def test_resolution_profile_and_old_raster_reopen(self):
  rev=self.d.import_bytes('resolutions.pdf',make_pdf(1));d,r=rev['document_id'],rev['revision_id']
  old=self.d.raster(d,r,1)
  changed=Documents(self.t.name,render_scale=1)
  low=changed.raster(d,r,1)
  self.assertEqual(old['width'],low['width']*2)
  self.assertNotEqual(old['render_profile'],low['render_profile'])
  self.assertEqual(changed.raster_by_hash(d,r,1,old['raster_hash'])['path'],old['path'])
 def test_actual_pptx_hidden_slides_without_notes(self):
  rev=self.d.import_bytes('generated.pptx',make_pptx());job=self.d.run_job(rev['job_id'])
  self.assertEqual(job['status'],'ready',job)
  saved=self.d.revision(rev['document_id'],rev['revision_id']);self.assertEqual(saved['page_count'],3)
  import pypdfium2 as pdfium
  doc=pdfium.PdfDocument(str(self.d.pdf_path(rev['document_id'],rev['revision_id'])));content=[]
  try:
   for i in range(len(doc)):
    page=doc[i];text=page.get_textpage();content.append(text.get_text_range());text.close();page.close()
  finally:doc.close()
  self.assertIn('SLIDE 2','\n'.join(content));self.assertNotIn('PRIVATE_NOTES_SENTINEL','\n'.join(content))
if __name__=='__main__':unittest.main()
