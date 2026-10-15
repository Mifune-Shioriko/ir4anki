"""Cold browser, remote requests rejected, native local service and isolated dist."""
import os,sys,subprocess,time,json
from pathlib import Path
from playwright.sync_api import sync_playwright
from native_fixture import cleanup,reserve_loopback_port,verify_server
from interleave_ui_test import prepare,ROOT,DIST
RUN=Path(os.environ['OFFLINE_RUN_DIR']);RUN.mkdir(parents=True,exist_ok=True)

def main():
    env=prepare(2,4,'offline')
    env.update(ANKI_SYNC_ENDPOINT='http://127.0.0.1:1/',ANKI_SYNC_HKEY='fixture-only',ANKI_SYNC_POLICY='manual')
    port=reserve_loopback_port();base=f'http://127.0.0.1:{port}'
    with (RUN/'cold-browser-server.log').open('w') as log:
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT/'backend',env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            verify_server(proc,base)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
                context=browser.new_context(viewport={'width':1400,'height':950})
                page=context.new_page()
                external=[];requests=[];errors=[];timings=[]
                def route(r):
                    if r.request.url.startswith(base+'/'):r.continue_()
                    else:external.append(r.request.url);r.abort()
                context.route('**/*',route)
                page.on('request',lambda r:requests.append(r.url))
                page.on('pageerror',lambda e:errors.append(str(e)))
                def state():return page.request.get(base+'/api/flow/state').json()
                page.goto(base,wait_until='networkidle')
                page.locator('md-filled-button:has-text("开始")').click();page.wait_for_selector('.reading-chunk-body')
                page.wait_for_selector('.katex')
                page.wait_for_function("() => Array.from(document.querySelectorAll('.reading-chunk-body img')).some(i => i.complete && i.naturalWidth > 0)")
                d=state();seq=[]
                while d['flow']['phase']!='complete':
                    if d['flow']['phase']=='reading':
                        seq.append('R')
                        with page.expect_response(lambda r:'/api/flow/state' in r.url and r.request.method=='GET') as response:
                            page.locator('md-text-button:has-text("下一张（稍后继续）")').click()
                        d=response.value.json()
                    else:
                        seq.append('C');page.locator('md-filled-tonal-button:has-text("显示答案")').click()
                        before=sum('/api/flow/state' in u for u in requests);start=time.perf_counter()
                        with page.expect_response(lambda r:'/api/answer?' in r.url) as response:page.locator('md-filled-button.ease-good').click()
                        d=response.value.json()['state']
                        selector={'review':'md-filled-tonal-button:has-text("显示答案")','reading':'.reading-chunk-body','complete':'.round-summary'}[d['flow']['phase']]
                        page.wait_for_selector(selector);timings.append(time.perf_counter()-start)
                        page.wait_for_load_state('networkidle')
                        assert sum('/api/flow/state' in u for u in requests)==before,'normal grade fetched full state'
                assert d['can_undo']
                page.locator('md-text-button:has-text("撤销上次评分")').click();page.wait_for_selector('md-filled-tonal-button:has-text("显示答案")')
                assert state()['flow']['phase']=='review'
                page.reload(wait_until='networkidle');page.wait_for_selector('md-filled-tonal-button:has-text("显示答案")')
                assert not external and not errors,(external,errors)
                evidence=dict(sequence=seq,click_to_next_seconds=timings,remote_requests=external,page_errors=errors,dist=str(DIST))
                (RUN/'cold-browser.json').write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence))
                browser.close()
        finally:proc.terminate();proc.wait(timeout=10);cleanup()
if __name__=='__main__':main()
