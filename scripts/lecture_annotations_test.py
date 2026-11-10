"""Standalone behavioral tests: only generated scratch content, no app import."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from lecture_annotations import Annotations, Conflict

class AnnotationTests(unittest.TestCase):
 def setUp(self):
  import os
  self.tmp=tempfile.TemporaryDirectory(dir=os.environ['NATIVE_FIXTURE_ROOT']); self.root=Path(self.tmp.name)
  self.a=Annotations(self.root); self.key=('d'*32,'e'*32,1)
 def tearDown(self): self.tmp.cleanup()
 def test_utf8_idempotent_external_conflict(self):
  empty=self.a.read(*self.key)
  first=self.a.save(*self.key,body='理解：$x^2$\n',expected=empty['version'],request_id='one')
  self.assertEqual(self.a.save(*self.key,body='理解：$x^2$\n',expected=empty['version'],request_id='one'),first)
  self.assertEqual(Path(first['markdown_path']).read_bytes(),'理解：$x^2$\n'.encode())
  Path(first['markdown_path']).write_text('external',encoding='utf-8')
  with self.assertRaises(Conflict):self.a.save(*self.key,body='overwrite',expected=first['version'],request_id='two')
  observed=self.a.read(*self.key)
  self.assertEqual(observed['body'],'external')
  self.assertNotEqual(observed['version'],first['version'])
 def test_crash_after_file_replace_recovers_index(self):
  def fault(phase):
   if phase=='replaced':raise RuntimeError('injected crash')
  with self.assertRaises(RuntimeError): self.a.save(*self.key,body='durable',expected=self.a.read(*self.key)['version'],request_id='crash',fault=fault)
  recovered=Annotations(self.root)
  self.assertEqual(recovered.read(*self.key)['body'],'durable')
  self.assertEqual(recovered.save(*self.key,body='durable',expected=hashlib.sha256(b'').hexdigest(),request_id='crash')['body'],'durable')
 def test_pages_isolated_clear_preserves_id(self):
  one=self.a.read(*self.key);two=self.a.read(self.key[0],self.key[1],2)
  self.assertNotEqual(one['annotation_id'],two['annotation_id'])
  saved=self.a.save(*self.key,body='x',expected=one['version'],request_id='a')
  clear=self.a.save(*self.key,body='',expected=saved['version'],request_id='b')
  self.assertEqual(clear['annotation_id'],one['annotation_id'])
  self.assertEqual(self.a.read(self.key[0],self.key[1],2)['body'],'')
 def test_request_collision_and_path_rejection(self):
  self.a.save(*self.key,body='a',expected=self.a.read(*self.key)['version'],request_id='a')
  with self.assertRaises(Conflict):self.a.save(*self.key,body='b',expected='bad',request_id='a')
  with self.assertRaises(ValueError):self.a.read('../escape','e'*32,1)
 def test_index_search_uses_real_external_bytes_and_page_identity(self):
  original=self.a.read(*self.key)
  saved=self.a.save(*self.key,body='first explanation',expected=original['version'],request_id='search')
  Path(saved['markdown_path']).write_text('external explanation updated',encoding='utf-8')
  hits=self.a.search(self.key[0],'updated')
  self.assertEqual(len(hits),1);self.assertEqual(hits[0]['page'],1)
  self.assertEqual(hits[0]['annotation_id'],original['annotation_id'])
  self.assertEqual(self.a.search('f'*32,'updated'),[])
if __name__=='__main__':unittest.main()
