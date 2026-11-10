"""Isolated verification ledger. Each child has its own exact exit status.
No operational collection/corpus/credentials, services or dist are used.
"""
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--run-root',required=True);p.add_argument('--dist',required=True);p.add_argument('--ui',action='store_true');p.add_argument('--conversion',action='store_true');p.add_argument('--only',action='append');args=p.parse_args()
 run=Path(args.run_root).resolve();scratch=Path('/home/shioriko/.hermes/cache/scratch').resolve()
 if not run.is_relative_to(scratch) or run==scratch:raise SystemExit('verification root must be a fresh scratch child')
 run.mkdir(parents=True,exist_ok=False);(run/'fixtures').mkdir();(run/'logs').mkdir()
 env={k:v for k,v in os.environ.items() if not k.startswith('ANKI_') and k!='ANKICONNECT_URL'}
 env.update(NATIVE_FIXTURE_ROOT=str(run/'fixtures'),TMPDIR=str(run/'fixtures'),PYTHONPYCACHEPREFIX=str(run/'pycache'),PYTHONUNBUFFERED='1',REGRESSION_DIST=str(Path(args.dist).resolve()),XDG_CACHE_HOME=str(run/'cache'))
 rows=[]
 def execute(name,command,cwd=ROOT,timeout=90):
  if args.only and name not in args.only:return
  command=list(command)
  if command[0]==sys.executable:
   if command[1:3]==['-m','pytest']:command=[sys.executable,str(ROOT/'scripts/lecture_test_driver.py'),'pytest']+command[3:]
   else:command=[sys.executable,str(ROOT/'scripts/lecture_test_driver.py')]+command[1:]
  start=time.monotonic();log=run/'logs'/f'{name}.log'
  with log.open('w') as f:
   child=subprocess.Popen(command,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
   try:code=child.wait(timeout=timeout)
   except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGKILL);child.wait();code=124;f.write('\nVERIFICATION TIMEOUT\n')
  text=log.read_text(errors='replace');blocker='sandbox socket EPERM' if 'PermissionError: [Errno 1] Operation not permitted' in text else None
  row=dict(suite=name,command=list(map(str,command)),cwd=str(cwd),exit_code=code,seconds=round(time.monotonic()-start,3),log=str(log),blocker=blocker)
  rows.append(row);(run/'verification.json').write_text(json.dumps(dict(cwd=str(ROOT),env_safe_overrides={k:v for k,v in env.items() if k in ['NATIVE_FIXTURE_ROOT','TMPDIR','PYTHONPYCACHEPREFIX','REGRESSION_DIST','XDG_CACHE_HOME']},cleared_environment=['ANKI_*','ANKICONNECT_URL'],results=rows),indent=2));print(json.dumps(row),flush=True)
 for name in ['documents','reading','annotations','occlusion','native','authoring_recovery','portable_html']:
  execute('lecture-'+name,[sys.executable,str(ROOT/f'scripts/lecture_{name}_test.py')])
 execute('lecture-resume-api',[sys.executable,'-m','pytest','-p','no:cacheprovider','-q','--basetemp='+str(run/'pytest-resume'),str(ROOT/'scripts/lecture_resume_api_test.py')])
 execute('lecture-api',[sys.executable,'-m','pytest','-p','no:cacheprovider','-q','--basetemp='+str(run/'pytest-lecture'),str(ROOT/'scripts/lecture_integration_test.py')])
 for name in ['bookmark_only','interleave_policy','interleave_recovery','pylib_durable','pylib_app','manual_sync_auth','review_withdrawal','offline_flow','offline_sync']:
  execute(name,[sys.executable,'-m','pytest','-p','no:cacheprovider','-q','--basetemp='+str(run/('pytest-'+name)),str(ROOT/f'scripts/{name}_test.py')])
 for name in ['offline_boot','offline_answer','pylib_markdown_api','files_relocation','files-protocol','files-recovery-index','interleave_integration','native_sync_deferral']:
  execute(name,[sys.executable,str(ROOT/f'scripts/{name}_test.py')])
 for test in sorted((ROOT/'frontend/tests').glob('*.test.mjs')):
  loader='solid-lifecycle-loader.mjs' if test.stem=='lecture-lifecycle.test' else 'loader.mjs'
  conditions=['--conditions=browser'] if test.stem=='lecture-lifecycle.test' else []
  execute('frontend-'+test.stem,['node',*conditions,'--loader','./tests/'+loader,str(test.relative_to(ROOT/'frontend'))],ROOT/'frontend')
 if args.conversion:execute('real-documents',[sys.executable,str(ROOT/'scripts/lecture_real_documents_test.py')],timeout=130)
 if args.ui:execute('lecture-ui',[sys.executable,str(ROOT/'scripts/lecture_ui_test.py'),'--dist',args.dist,'--output',str(run/'ui')],timeout=180)
 return 1 if any(x['exit_code'] for x in rows) else 0
if __name__=='__main__':raise SystemExit(main())
