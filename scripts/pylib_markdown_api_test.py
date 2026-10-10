"""In-process API verification with a real temporary Collection; no sockets/sync."""
import asyncio, importlib, os, sys, tempfile
from pathlib import Path
import httpx
from anki.collection import Collection

ROOT=Path(__file__).resolve().parents[1]
async def run():
 # Short heartbeat also makes thread completions observable in restricted
 # environments where selector wakeups can be suppressed.
 async def heartbeat():
  while True: await asyncio.sleep(.05)
 pulse=asyncio.create_task(heartbeat())
 with tempfile.TemporaryDirectory(prefix='ir4anki-markdown-api-') as temp:
  root=Path(temp); notes=root/'notes'; notes.mkdir(); (notes/'source.md').write_text('# Source\n\nTest source.\n')
  col=Collection(str(root/'collection.anki2')); col.close()
  for key in list(os.environ):
   if key.startswith('ANKI_SYNC_'): del os.environ[key]
  os.environ.update(ANKI_BACKEND='pylib',ANKI_COLLECTION_PATH=str(root/'collection.anki2'),ANKI_STATE_DIR=str(root/'state'),ANKI_NOTES_DIR=str(notes),ANKI_BACKUP_DIR=str(root/'backups'),ANKI_ADD_MODEL='Basic',ANKI_ADD_CLOZE_MODEL='Cloze',ANKI_PREVIEW_MODE='1',ANKI_READING_MODE='1',REVIEW_DIST_DIR=str(root/'no-dist'))
  sys.path.insert(0,str(ROOT/'backend')); app=importlib.import_module('app')
  print('Temporary engine imported', flush=True)
  # Keep this persistence verification independent of background sync jobs.
  app.fire_and_forget_sync=lambda:None
  async with app.app.router.lifespan_context(app.app):
   print('Temporary engine started', flush=True)
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url='http://temporary') as client:
    for kind,fields in [('qa',{'Front':'  **Question** <br>\n\n- list\n','Back':'$x_{2}$ ![](/media/image.png)\n\n| A | B |\n|---|---|\n| 1 | 2 |'}),('cloze',{'Text':'**{{c1::$C_{5,6}$::hint}}** [{{c2::other}}](https://example.org)\n\n```\nsource\n```','Back Extra':'extra <img src=x>\n'})]:
     result=await client.post('/api/card/add',json={'kind':kind,'fields':fields,'tags':['user-tag','ir4anki::markdown']})
     assert result.status_code==200,result.text
     data=result.json(); cid=data['cardIds'][0]
     note=(await client.get('/api/note',params={'card_id':cid})).json()
     assert note['fields']==fields,note
     assert set(['user-tag','ir4anki::markdown']).issubset(note['tags'])
     if kind=='cloze':
      assert len(data['cardIds'])==2
      assert note.get('kind')=='cloze' and note.get('ord') in (0,1), note
      second=(await client.get('/api/note',params={'card_id':data['cardIds'][1]})).json()
      assert {note.get('ord'),second.get('ord')} == {0,1}, (note,second)
     changed={**fields}; first=next(iter(changed)); changed[first]+='\n\n**edited**\n'
     result=await client.post('/api/note/update',json={'card_id':cid,'fields':changed,'tags':note['tags']})
     assert result.status_code==200,result.text
     reopened=(await client.get('/api/note',params={'card_id':cid})).json()
     assert reopened['fields']==changed
     assert set(['user-tag','ir4anki::markdown']).issubset(reopened['tags'])
     print(f'PASS real Collection {kind}: exact Markdown storage, tags, reopen/edit'+(' and two native cloze ordinals' if kind=='cloze' else ''))
    legacy={'Front':'<b>legacy</b>','Back':'<div>HTML</div>'}
    r=await client.post('/api/card/add',json={'fields':legacy,'tags':['legacy']}); assert r.status_code==200,r.text
    cid=r.json()['cardIds'][0]; note=(await client.get('/api/note',params={'card_id':cid})).json()
    assert note['fields']==legacy and 'ir4anki::markdown' not in note['tags']
    print('PASS legacy HTML stays HTML with no migration')
if __name__=='__main__': asyncio.run(run())
