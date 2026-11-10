import sys,unittest,tempfile,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from lecture_documents import Documents
from lecture_store import Conflict
class DocumentsTests(unittest.TestCase):
 def setUp(self):self.t=tempfile.TemporaryDirectory(dir=os.environ['NATIVE_FIXTURE_ROOT']);self.d=Documents(Path(self.t.name))
 def tearDown(self):self.t.cleanup()
 def test_immutable_versions_and_rename(self):
  # injected inspector exercises storage independently; real PDF suite below
  inspect=lambda path:{'pages':[{'width':640,'height':480,'rotation':0}],'page_count':1}
  a=self.d.import_bytes('中文/讲义.pdf',b'%PDF-generated-one',inspect=inspect)
  doc=self.d.document(a['document_id']);doc.update(listed=True,reading_revision=a['revision_id'],position=3);self.d.put('documents',a['document_id'],doc)
  b=self.d.import_bytes('中文/讲义.pdf',b'%PDF-generated-two',inspect=inspect)
  self.assertEqual(self.d.document(a['document_id'])['reading_revision'],a['revision_id'])
  self.assertEqual(self.d.document(a['document_id'])['position'],3)
  self.assertEqual(a['document_id'],b['document_id']);self.assertNotEqual(a['revision_id'],b['revision_id'])
  self.assertEqual(self.d.pdf_path(a['document_id'],a['revision_id']).read_bytes(),b'%PDF-generated-one')
  self.d.relocate('中文','新目录',True)
  self.assertEqual(self.d.document(a['document_id'])['path'],'新目录/讲义.pdf')
  self.assertEqual(self.d.revision(a['document_id'],a['revision_id'])['page_count'],1)
 def test_bad_inputs_do_not_create_ready_documents(self):
  for name,body in [('../x.pdf',b'bad'),('a.pdf',b'bad'),('x.exe',b'%PDF-bad')]:
   with self.assertRaises(ValueError):self.d.import_bytes(name,body)
  self.assertEqual(self.d.list(),[])
 def test_retry_job_retains_original_and_identity(self):
  import io,zipfile
  data=io.BytesIO()
  with zipfile.ZipFile(data,'w') as z:z.writestr('[Content_Types].xml','<Types/>');z.writestr('ppt/presentation.xml','<p/>')
  a=self.d.import_bytes('a.pptx',data.getvalue());self.assertEqual(a['status'],'waiting')
  self.assertTrue(self.d.original_path(a['document_id'],a['revision_id']).exists())
  def fail(*args):raise RuntimeError('converter missing')
  self.d.run_job(a['job_id'],converter=fail)
  job=self.d.job(a['job_id']);self.assertEqual(job['status'],'failed');self.assertIn('converter missing',job['error'])
  self.d.retry(a['job_id']);self.assertEqual(self.d.job(a['job_id'])['status'],'waiting')
 def test_pptx_nested_external_svg_is_rejected_before_converter(self):
  import io,zipfile
  data=io.BytesIO()
  with zipfile.ZipFile(data,'w') as z:
   z.writestr('ppt/presentation.xml','<p/>')
   z.writestr('ppt/media/image.svg','<svg xmlns="http://www.w3.org/2000/svg"><image href="https://external.invalid/image.png"/></svg>')
  with self.assertRaises(ValueError):self.d.import_bytes('external.pptx',data.getvalue())
  self.assertEqual(self.d.all('jobs'),[])
if __name__=='__main__':unittest.main()
