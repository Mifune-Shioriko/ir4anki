#!/usr/bin/env python3
"""Run the 13 migrated behavior suites and retained connect fault suite."""
import argparse,json,os,re,signal,subprocess,sys,time
from pathlib import Path
SUITES='list_dealing gate_segment_level auto_release reading_edit split_guard file_mgmt reading_ui modes_ui seg_edit_ui split_ui file_mgmt_ui flat_panels_ui desktop_ui sync_throttle native_sync_deferral'.split()
def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',action='append',choices=SUITES);p.add_argument('--dist',default='/tmp/ir4anki-regression-dist');p.add_argument('--output',default='/tmp/ir4anki-remaining/regression-results.json');p.add_argument('--timeout',type=int,default=180);args=p.parse_args()
    rows=[]; logs=Path(args.output).with_suffix('');logs.mkdir(parents=True,exist_ok=True)
    for suite in args.suite or SUITES:
        env={k:v for k,v in os.environ.items() if not k.startswith('ANKI_')};env['REGRESSION_DIST']=args.dist;env['PYTHONUNBUFFERED']='1'
        start=time.monotonic()
        proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name(suite+'_test.py'))],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=args.timeout)
            output=stdout+stderr;code=proc.returncode
        except subprocess.TimeoutExpired:
            # Terminate only the process group this runner created, including
            # disposable uvicorn descendants, so timeout cannot leak owners.
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                stdout, stderr = proc.communicate()
            output=stdout+stderr+'\nSUITE TIMEOUT\n';code=124
        (logs/(suite+'.log')).write_text(output)
        match=re.findall(r'(\d+) passed, (\d+) failed',output)
        blocker='socket creation denied by sandbox' if 'PermissionError: [Errno 1] Operation not permitted' in output else ('suite timeout' if code==124 else None)
        row=dict(blocker=blocker, suite=suite,coverage='retained-connect/fault-injection' if suite=='sync_throttle' else 'native',exit_code=code,seconds=round(time.monotonic()-start,2),passed=int(match[-1][0]) if match else len(re.findall(r'^\s*ok\s',output,re.M)),failed=int(match[-1][1]) if match else len(re.findall(r'^\s*FAIL\s',output,re.M)),log=str(logs/(suite+'.log')))
        rows.append(row);Path(args.output).write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
    return int(any(row['exit_code'] for row in rows))
if __name__=='__main__':sys.exit(main())
