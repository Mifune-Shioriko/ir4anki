"""Real relocation IO in disposable directories, no app/engine/collection."""
import importlib.util,os,tempfile,json
from pathlib import Path
from unittest.mock import patch
source=Path(os.environ.get('RELOCATION_SOURCE',Path(__file__).resolve().parents[1]/'backend/file_relocation.py'))
spec=importlib.util.spec_from_file_location('relocation_under_test',source);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory(prefix='files-index-',dir=os.environ.get('TMPDIR')) as tmp:
 root=Path(tmp)/'corpus';root.mkdir();state=Path(tmp)/'state';state.mkdir()
 moves=[]
 def relocate():return m.Relocator(root,state,lambda *a:None,lambda *a:moves.append(a[:4]),lambda *a:False,lambda *a:'unchanged')
 r=relocate();r.recover()
 for n in range(250):(r.directory/(f'{n:032x}'+'.json')).write_text(json.dumps({'phase':'complete','fingerprint':['synthetic']*100}))
 reads=[];read=Path.read_text
 def tracked(p,*a,**kw):
  if p.parent==r.directory and p.suffix=='.json':reads.append(p.name)
  return read(p,*a,**kw)
 with patch.object(Path,'read_text',tracked):relocate().recover()
 assert reads==[],f'completed journal reads: {len(reads)}'
 print('PASS 250 completed records: new instance ordinary recovery reads zero journals')
 (root/'a.md').write_text('original')
 class Crash(BaseException):pass
 r.fault=lambda phase: (_ for _ in ()).throw(Crash()) if phase=='fs' else None
 try:r.move('a.md','b.md',False)
 except Crash:pass
 reads.clear()
 with patch.object(Path,'read_text',tracked):relocate().recover()
 assert len(reads)==1,reads
 assert (root/'b.md').read_text()=='original' and not (root/'a.md').exists()
 assert len(moves)==1
 print('PASS restart reads only one pending journal; completes FS-boundary recovery')
 # A durable marker without a durable journal precedes any FS mutation.
 orphan=r.directory/'pending'/('f'*32);orphan.touch();relocate().recover();assert not orphan.exists()
 print('PASS pre-journal interrupted marker recovery touches no corpus data')
 # Legacy journals are retained byte-for-byte; one-time index marks only pending.
 marker=r.directory/'indexed-v1';marker.unlink();before={p.name:p.read_bytes() for p in r.directory.glob('*.json')}
 relocate().recover();assert before=={p.name:p.read_bytes() for p in r.directory.glob('*.json')}
 reads.clear()
 with patch.object(Path,'read_text',tracked):relocate().recover()
 assert reads==[]
 print('PASS legacy indexing preserves all journals and does not repeat scan')
