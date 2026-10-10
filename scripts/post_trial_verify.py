"""Safe independent post-trial verification. Never reads operator configuration.
All native fixtures/artifacts/builds are below the required Hermes scratch root.
Exit nonzero for blocked UI/socket tests; do not misreport a build as UI coverage.
"""
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SCRATCH=Path('/home/shioriko/.hermes/cache/scratch').resolve()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output');args=p.parse_args()
 run=Path(args.output).resolve() if args.output else Path(tempfile.mkdtemp(prefix='post-trial-',dir=SCRATCH))
 if not run.is_relative_to(SCRATCH) or run==SCRATCH: p.error('--output must be a subdirectory of Hermes scratch')
 run.mkdir(parents=True,exist_ok=True)
 env={k:v for k,v in os.environ.items() if not k.startswith('ANKI_')}
 env.update(TMPDIR=str(run),NATIVE_FIXTURE_ROOT=str(run),REGRESSION_DIST=str(run/'dist'),INTERLEAVE_RUN_DIR=str(run/'interleave-ui'),POST_TRIAL_RUN=str(run/'ui'),PYTHONDONTWRITEBYTECODE='1')
 py=sys.executable
 commands=[
 ('typecheck',[str(ROOT/'frontend/node_modules/.bin/tsc'),'-p','frontend/tsconfig.app.json','--noEmit','--incremental','false']),
 ('markdown',['npm','--prefix','frontend','run','test:markdown']),
 ('build',['npm','--prefix','frontend','run','build','--','--outDir',str(run/'dist')]),
 ('latency',[py,'scripts/post_trial_test.py']),
 ('mixed-api',[py,'scripts/post_trial_api_test.py']),
 ('markdown-api',[py,'scripts/pylib_markdown_api_test.py']),
 ('native-pytest',[py,'-m','pytest','-q','-p','no:cacheprovider','--basetemp='+str(run/'pytest'),'scripts/pylib_backend_test.py','scripts/pylib_app_test.py','scripts/pylib_durable_test.py','scripts/pylib_durable_sync_test.py','scripts/pylib_capability_test.py','scripts/pylib_orphan_test.py']),
 ('policy',[py,'scripts/interleave_policy_test.py']),
 ('interleave-api',[py,'scripts/interleave_integration_test.py']),
 ('recovery',[py,'scripts/interleave_recovery_test.py']),
 ('regression',[py,'scripts/regression_runner.py','--dist',str(run/'dist'),'--output',str(run/'regression.json')]),
 ('post-ui',[py,'scripts/post_trial_ui_test.py']),
 ('interleave-ui',[py,'scripts/interleave_ui_test.py']),
 ]
 rows=[]
 for name,cmd in commands:
  start=time.monotonic()
  proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
  try: out,_=proc.communicate(timeout=180);code=proc.returncode
  except subprocess.TimeoutExpired:
   import signal
   os.killpg(proc.pid,signal.SIGTERM)
   try: out,_=proc.communicate(timeout=10)
   except subprocess.TimeoutExpired: os.killpg(proc.pid,signal.SIGKILL);out,_=proc.communicate()
   out+='\nTIMEOUT\n';code=124
  (run/(name+'.log')).write_text(out)
  row=dict(name=name,command=cmd,exit_code=code,seconds=round(time.monotonic()-start,3),log=str(run/(name+'.log')))
  rows.append(row);(run/'checks.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
 return int(any(r['exit_code'] for r in rows))
if __name__=='__main__': sys.exit(main())
