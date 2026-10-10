"""Mixed HTML/Markdown note types and non-contiguous cloze ordinals, real native API."""
import asyncio, os, sys
from pathlib import Path
from native_fixture import configure, NativeData, run_closed
for key in list(os.environ):
 if key.startswith('ANKI_'): os.environ.pop(key)
os.environ.update(ANKI_PREVIEW_MODE='1',ANKI_READING_MODE='1')
configure()
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import app as b
import httpx
b.fire_and_forget_sync=lambda:None
async def run():
 native=NativeData(b);identities={}
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b.app),base_url='http://fixture') as c:
  for kind in ['qa','cloze']:
   for markdown in [False,True]:
    fields=({'正面':'**mixed** <b>legacy</b> $x_{2}$','背面':'<div>answer</div>'} if kind=='qa' else {'文字':'**{{c3::$x_{2}$::提示}}** <b>{{c7::另一答案}}</b>','背面额外':'extra <br> **mixed**'})
    tags=['user-tag']+(['ir4anki::markdown'] if markdown else [])
    r=await c.post('/api/card/add',json=dict(kind=kind,fields=fields,tags=tags));assert r.status_code==200,r.text
    created=r.json();cards=created['cardIds'];cid=cards[0]
    stored=native.native(lambda col: (col.get_card(cid).note().mid,col.get_card(cid).note().note_type()['type']))
    if kind in identities: assert identities[kind]==stored,'storage format must not create a new note type'
    identities[kind]=stored
    ords=native.native(lambda col: {col.get_card(i).ord for i in cards})
    assert ords==({2,6} if kind=='cloze' else {0})
    note=(await c.get('/api/note',params=dict(card_id=cid))).json();assert note['fields']==fields and set(note['tags'])==set(tags)
    changed={**fields};first=next(iter(changed));changed[first]+='\n\n<em>tail</em> **追加**'
    r=await c.post('/api/note/update',json=dict(card_id=cid,fields=changed,tags=note['tags']));assert r.status_code==200,r.text
    reopened=(await c.get('/api/note',params=dict(card_id=cid))).json()
    assert reopened['fields']==changed and set(reopened['tags'])==set(tags)
    assert native.native(lambda col: set(col.get_card(cid).note().card_ids()))==set(cards)
    assert native.native(lambda col: {col.get_card(i).ord for i in cards})==ords
    print(f'PASS {kind} markdown={markdown}: exact mixed fields/save/reopen/tags/type/card identities/ordinals')
 assert identities['qa'][1]==0 and identities['cloze'][1]==1
 assert identities['qa'][0]!=identities['cloze'][0]
if __name__=='__main__': asyncio.run(run_closed(b,run()))
