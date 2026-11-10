"""Shared disposable seed for first-render DTO fixture / later native launcher.
No live origins. Backend import happens only after explicit environment isolation.
"""
import os,sys,json,asyncio,argparse
from pathlib import Path

def seed_fixture(root):
 root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
 assert root.is_relative_to(Path('/home/shioriko/.hermes/cache/scratch'))
 for k in list(os.environ):
  if k.startswith('ANKI_') or k=='ANKICONNECT_URL':os.environ.pop(k)
 os.environ['NATIVE_FIXTURE_ROOT']=str(root)
 from native_fixture import configure
 collection=configure()
 base=collection.parent
 for key,leaf in [('ANKI_STATE_DIR','state'),('ANKI_NOTES_DIR','corpus'),('ANKI_MEDIA_DIR','collection.media'),('ANKI_READING_DB','state/reading.db'),('ANKI_BACKUP_DIR','backups'),('ANKI_EXPORT_DIR','exports')]:os.environ[key]=str(base/leaf)
 os.environ.update(ANKI_BACKEND='pylib',ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='0',ANKI_SYNC_POLICY='manual',ANKI_SYNC_ENDPOINT='',ANKI_SYNC_USERNAME='',ANKI_SYNC_PASSWORD='',ANKI_SYNC_HKEY='')
 # REVIEW_DIST_DIR supplied later only for isolated native server, never live.
 os.environ.pop('REVIEW_DIST_DIR',None)
 corpus=Path(os.environ['ANKI_NOTES_DIR']);corpus.mkdir(parents=True,exist_ok=True)
 long='长文件名完整保留'*9+' 🟣 Unicode אבג.md'
 assert len(long.encode())<=255
 notes={'笔记/入门.md':'# 入门\n\n这是隔离测试的 Markdown 原文。\n\n数学 $x^2$ 与中文。\n','笔记/第二篇.md':'# 第二篇\n\n用于真实阅读清单接入的合成文本。\n','欢迎.md':'# 欢迎\n\n文件预览验证。\n','笔记/'+long:'# 长名\n\n保留 Unicode 名称和扩展名。\n'}
 notes.update({f'列表 {i:02}.md':f'# 合成条目 {i}\n\n滚动容器测试。\n' for i in range(48)})
 for directory in ['笔记/空目录','归档','空目录']:(corpus/directory).mkdir(parents=True,exist_ok=True)
 for p,text in notes.items():
  path=corpus/p;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 # Actual native note/card exists; HTTP state below is intentionally synthetic.
 from anki.collection import Collection
 col=Collection(str(collection))
 try:
  note=col.new_note(col.models.by_name('问答题'));note['正面']='合成活动轮次';note['背面']='用于验证文件快捷键不触发学习操作';col.add_note(note,col.decks.id('2026'));cid=note.cards()[0].id
 finally:col.close()
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
 import app
 async def admission():
  for p in ['笔记/入门.md','笔记/第二篇.md']:await app.reading_list_add(app.ReadingPathBody(path=p))
  listing=await app.files_list();members=await app.files_membership()
  return listing,members
 listing,members=asyncio.run(admission());admitted=app._reading_read()['list']
 assert len(admitted)==2 and all(len(x['segments'])==1 for x in admitted)
 assert set(members['paths'])=={x['path'] for x in admitted}
 slots=sum(len(x['segments']) for x in admitted)
 state=dict(state='active',cards=[dict(cardId=cid,question='合成活动轮次',answer='用于验证文件快捷键不触发学习操作',fields={'正面':'合成活动轮次','背面':'用于验证文件快捷键不触发学习操作'},tags=[],ord=0,deckName='2026',modelName='问答题',css='',isNew=True,due=0)],done=0,total=1,can_undo=True,due_remaining=0,new_total=1,reading_mode=True,reading_list_size=2,reading_available=2,reading_dealable=2,reading_round={'status':'idle','chunks':[],'done':slots,'total':slots},preview_mode=False,flow=dict(id='synthetic-first-render',policy='even-floor-v1',phase='review',reading_consumed=slots,reading_slots=slots,card_handled=0,card_answered=0,effective_card_count=1,card_count=1,blocks=[1],stats=dict(readingDone=slots,readingSkipped=0,readingNext=0,reviewed=0,newReviewed=0)))
 return dict(scope='Actual App with intercepted synthetic HTTP DTOs; native seeding/admission verified separately; no backend HTTP E2E claim',listing=listing,membership=members,raw=notes,admission=admitted,state=state,paths=dict(root=str(base),collection=str(collection),corpus=str(corpus),state=os.environ['ANKI_STATE_DIR'],reading_db=os.environ['ANKI_READING_DB'],media=os.environ['ANKI_MEDIA_DIR'],backups=os.environ['ANKI_BACKUP_DIR'],exports=os.environ['ANKI_EXPORT_DIR']),long_path='笔记/'+long)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True);p.add_argument('--seed-only',action='store_true');args=p.parse_args()
 try:
  seed=seed_fixture(args.root);out=Path(args.output).resolve();assert out.is_relative_to(Path('/home/shioriko/.hermes/cache/scratch'));out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(seed,ensure_ascii=False,indent=2));print('PASS isolated seed: real native collection, 2 admitted Markdown fragments, '+str(len(seed['listing']['files']))+' list files; DTO/path construction complete')
 finally:
  from native_fixture import cleanup
  cleanup()
