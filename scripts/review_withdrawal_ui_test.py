"""Real browser proof on a disposable restored native incident, never live data."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from native_fixture import configure, cleanup, reserve_loopback_port, verify_server
from pylib_test_support import run_alive
from review_withdrawal_test import incident
ROOT=Path(__file__).resolve().parents[1]
RUN=Path(os.environ['INTERLEAVE_RUN_DIR']).resolve()
DIST=Path(os.environ['REGRESSION_DIST']).resolve()
FIXTURE_ROOT=Path(os.environ['NATIVE_FIXTURE_ROOT']).resolve()
assert DIST != ROOT/'frontend/dist' and (DIST/'index.html').is_file()
assert DIST.is_relative_to(FIXTURE_ROOT.parent) and RUN.is_relative_to(FIXTURE_ROOT.parent)
sys.path.insert(0,str(ROOT/'backend'))


def capture_page(page, stem, *, errors, console, network, flows, failure=None):
    """Capture the existing disposable page only; no diagnostic API requests."""
    capture_errors=[]
    def save(suffix, action):
        try:action(RUN/f'{stem}.{suffix}')
        except Exception as exc:capture_errors.append({'artifact':suffix,'error':str(exc)})
    save('html',lambda path:path.write_text(page.content()))
    save('png',lambda path:page.screenshot(path=str(path),full_page=True,timeout=5000))
    save('buttons.json',lambda path:path.write_text(json.dumps(page.locator('md-filled-tonal-button').evaluate_all(
        "elements => elements.map(e => ({html:e.outerHTML, text:e.textContent, innerText:e.innerText, shadow:e.shadowRoot?.innerHTML}))"
    ),ensure_ascii=False,indent=2)))
    (RUN/f'{stem}.json').write_text(json.dumps({'url':page.url,'failure':failure,
        'page_errors':errors,'console':console,'network':network,'observed_flow_responses':flows,
        'capture_errors':capture_errors},ensure_ascii=False,indent=2))


def wait_for_reveal(page, stem, *, errors, console, network, flows):
    # Probe the existing ActionArea custom element, without clicking or grading.
    # Use the original exact locator if it works. Change locator only when the
    # real DOM proves that the aria-hidden Space keycap defeats exact text.
    candidate=page.locator('md-filled-tonal-button:has-text("显示答案")')
    candidate.wait_for(state='visible')
    capture_page(page,stem,errors=errors,console=console,network=network,flows=flows)
    exact=page.get_by_text('显示答案',exact=True)
    proof=candidate.evaluate("""element => {
        const copy=element.cloneNode(true);
        copy.querySelectorAll('.keycap').forEach(k => k.remove());
        return {html:element.outerHTML, text:element.textContent,
                labelWithoutKeycap:copy.textContent,
                keycaps:[...element.querySelectorAll('.keycap')].map(k => ({text:k.textContent,hidden:k.getAttribute('aria-hidden')}))};
    }""")
    proof['exact_text_matches']=exact.count()
    proof['selected_locator']='original exact text'
    if proof['exact_text_matches']==1 and exact.is_visible():
        exact.wait_for(state='visible')
    else:
        proof['selected_locator']='pending DOM validation'
        (RUN/f'{stem}.locator-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
        compact=lambda value:''.join(value.split())
        assert proof['exact_text_matches']==0,proof
        assert compact(proof['labelWithoutKeycap'])=='显示答案',proof
        assert compact(proof['text'])=='显示答案Space',proof
        assert proof['keycaps']==[{'text':'Space','hidden':'true'}],proof
        proof['selected_locator']='proven ActionArea button with aria-hidden Space keycap'
        assert candidate.is_visible()
    (RUN/f'{stem}.locator-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))


def main():
    # Normal socket attempt: EPERM is a visible blocker, never bypassed.
    port=reserve_loopback_port();base=f'http://127.0.0.1:{port}'
    RUN.mkdir(parents=True,exist_ok=True)
    env={k:v for k,v in os.environ.items() if not k.startswith('ANKI_') and k!='ANKICONNECT_URL'}
    env.update(ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='1',ANKI_REVIEW_DECK='2026',REVIEW_DIST_DIR=str(DIST))
    configure(env)
    original=dict(os.environ);proc=None
    try:
        os.environ.clear();os.environ.update(env)
        sys.modules.pop('app',None);b=importlib.import_module('app')
        b.fire_and_forget_sync=lambda:None
        async def setup():
            try:
                await b.anki('findCards',{'query':''});await incident(b)
            finally:await b._pylib.close()
        run_alive(setup())
        os.environ.clear();os.environ.update(original)
        with (RUN/'withdrawal-server.log').open('w') as log:
            proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT/'backend',env=env,stdout=log,stderr=subprocess.STDOUT)
            verify_server(proc,base)
            with sync_playwright() as p:
                browser=p.chromium.launch()
                try:
                    page=browser.new_page(viewport={'width':1400,'height':950})
                    page.route('**/*',lambda route:route.continue_() if urlparse(route.request.url).netloc==urlparse(base).netloc else route.abort())
                    errors=[];console=[];network=[];flows=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('console',lambda message:console.append({'type':message.type,'text':message.text}))
                    page.on('requestfailed',lambda request:network.append({'event':'requestfailed','method':request.method,'url':request.url,'failure':request.failure}))
                    def response_seen(response):
                        network.append({'event':'response','method':response.request.method,'url':response.url,'status':response.status})
                        if urlparse(response.url).path=='/api/flow/state':
                            try:flows.append(response.json())
                            except Exception as exc:flows.append({'capture_error':str(exc)})
                    page.on('response',response_seen)
                    try:
                        page.goto(base,wait_until='networkidle')
                        for refresh in (False,True):
                            if refresh:page.reload(wait_until='networkidle')
                            wait_for_reveal(page,'withdrawal-reload' if refresh else 'withdrawal-initial',
                                            errors=errors,console=console,network=network,flows=flows)
                            ring=page.get_by_role('progressbar',name='复习',exact=True)
                            ring.wait_for(state='visible')
                            assert ring.is_visible()
                            assert ring.get_attribute('aria-valuenow')=='0'
                            assert ring.get_attribute('aria-valuemax')=='119'
                            assert ring.get_attribute('aria-valuetext')=='复习 0/119'
                            assert ring.inner_text().replace('\n','').replace(' ','').startswith('0/119')
                            assert page.get_by_text('Q10045',exact=True).is_visible()
                            response=page.request.get(base+'/api/flow/state');assert response.ok
                            d=response.json()
                            assert (d['flow']['phase'],d['flow']['block_target'])==('review',18)
                            assert d['cards'][0]['cardId']==10045
                            assert d['flow']['stats']['reviewed']==0
                        assert not errors,errors
                        page.screenshot(path=str(RUN/'withdrawal-0-of-119.png'))
                        (RUN/'withdrawal-ui-proof.json').write_text(json.dumps({'phase':d['flow']['phase'],'owed':d['flow']['block_target'],'progress':'0/119','native_card':10045,'refresh':True,'page_errors':errors},indent=2))
                    except Exception as exc:
                        capture_page(page,'withdrawal-failure',errors=errors,console=console,
                                     network=network,flows=flows,failure=repr(exc))
                        raise
                finally:browser.close()
    finally:
        os.environ.clear();os.environ.update(original)
        if proc:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        cleanup();sys.modules.pop('app',None)

if __name__=='__main__':main()
