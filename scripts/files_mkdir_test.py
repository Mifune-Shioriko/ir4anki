"""Disposable stdlib regression. No inherited app paths, live server or collections."""
import os,sys,tempfile,asyncio,unittest
from pathlib import Path
for k in list(os.environ):
 if k.startswith('ANKI_') or k=='ANKICONNECT_URL':os.environ.pop(k)
root=Path(tempfile.mkdtemp(prefix='mkdir-test-',dir=os.environ['NATIVE_FIXTURE_ROOT']))
for key,leaf in [('ANKI_STATE_DIR','state'),('ANKI_NOTES_DIR','corpus'),('ANKI_COLLECTION_PATH','unused/collection.anki2'),('ANKI_MEDIA_DIR','media'),('ANKI_READING_DB','reading.db'),('ANKI_BACKUP_DIR','backups'),('ANKI_EXPORT_DIR','exports')]:os.environ[key]=str(root/leaf)
os.environ.update(ANKI_BACKEND='pylib',ANKI_SYNC_POLICY='manual',ANKI_SYNC_ENDPOINT='',ANKI_SYNC_HKEY='')
(root/'corpus').mkdir()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import app
from fastapi import HTTPException
class Mkdir(unittest.TestCase):
 def call(self,directory,name):return asyncio.run(app.files_mkdir(app.FilesMkdirBody(dir=directory,name=name)))
 def test_creation_and_no_collection(self):
  out=self.call('','中文 🟣');self.assertEqual(out['path'],'中文 🟣');self.assertTrue((root/'corpus/中文 🟣').is_dir())
  self.assertEqual(self.call('中文 🟣','空目录')['path'],'中文 🟣/空目录')
  self.assertFalse((root/'unused/collection.anki2').exists());self.assertFalse((root/'reading.db').exists())
 def test_empty_corpus_root(self):
  from unittest.mock import patch
  with patch.object(app,'NOTES_DIR',root/'new-corpus'):
   out=self.call('','首个空文件夹');self.assertEqual(out['path'],'首个空文件夹');self.assertTrue((root/'new-corpus/首个空文件夹').is_dir())
  self.assertFalse((root/'unused/collection.anki2').exists())
 def test_rejection(self):
  for directory,name in [('', ''),('','.'),('','..'),('','../x'),('','a/b'),('','a\\b'),('','_assets'),('','page-notes'),('','.hidden'),('','nul\x00x'),('','长'*86),('','a\nb'),('../','x'),('/','x'),('a/../b','x'),('_assets','x'),('page-notes','x'),('node_modules','x'),('missing','x')]:
   with self.subTest(directory=directory,name=name),self.assertRaises(HTTPException):self.call(directory,name)
  (root/'corpus/existing').mkdir(exist_ok=True)
  with self.assertRaises(HTTPException):self.call('','existing')
  outside=root/'outside';outside.mkdir(exist_ok=True);(root/'corpus/escape').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(HTTPException):self.call('escape','x')
  self.assertFalse((outside/'x').exists())
 def test_no_side_effect_rejection(self):
  self.assertFalse((root/'corpus/missing').exists())
if __name__=='__main__':
 import shutil
 try:unittest.main()
 finally:shutil.rmtree(root)
