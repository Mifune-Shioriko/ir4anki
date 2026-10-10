"""Playwright against disposable native uvicorn ONLY; requires isolated built dist.
Sequence/ratios, refresh in both phases, cross-phase undo, trace return,
split/removal, interrupted/legacy adoption, end-reading, exit and summary.
"""
import importlib,os,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from native_fixture import configure,NativeData,cleanup,reserve_loopback_port,verify_server
from pylib_test_support import run_alive
ROOT=Path(__file__).resolve().parents[1]
RUN=Path(os.environ.get('INTERLEAVE_RUN_DIR','/tmp/interleave-ui-evidence'));RUN.mkdir(parents=True,exist_ok=True)
DIST=Path(os.environ['REGRESSION_DIST']).resolve()
assert DIST != ROOT/'frontend'/'dist' and (DIST/'index.html').is_file(), 'isolated build required'
sys.path.insert(0,str(ROOT/'backend'))

def prepare(s,c,kind):
    # Native asyncio setup must run before entering sync_playwright(). Each
    # case owns a fresh collection AND fresh state/corpus paths. Never inherit
    # ANKI settings from a live installation or another disposable case.
    original_env = dict(os.environ)
    case_env = {key:value for key,value in original_env.items() if not key.startswith('ANKI_')}
    case_env.update(ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='1',REVIEW_DIST_DIR=str(DIST))
    configure(case_env)
    try:
        os.environ.clear()
        os.environ.update(case_env)
        sys.modules.pop('app',None)
        b=importlib.import_module('app')
        b.fire_and_forget_sync=lambda:None
        async def seed():
            try:
                await b.anki('findCards',{'query':''});native=NativeData(b)
                for i in range(c):native.add_card(500+i,deck='2026',note=5000+i)
                for i in range(s):
                    name=f'read-{i}.md'
                    (Path(os.environ['ANKI_NOTES_DIR'])/name).write_text('# Topic\n\nFirst paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n')
                    await b.reading_list_add(b.ReadingPathBody(path=name))
                if s and c:
                    data=b._reading_read();key=str(data['list'][0]['segments'][0]['seg_id'])
                    b._reading_record_card({'path':'read-0.md','chunk_key':key},5000)
                if kind in ('legacy','interrupted'):
                    await b._reading_start_impl()
                    if kind=='interrupted':
                        b._flow_write({'status':'initializing','mode':b.DEFAULT_MODE})
                        await b._start_session_impl(False)
            finally:
                await b._pylib.close()
        run_alive(seed())
        return case_env
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def run_case(browser,s,c,kind,case_env):
    port=reserve_loopback_port();base=f'http://127.0.0.1:{port}'
    logfile=(RUN/f'ui-server-{s}-{c}-{kind}.log').open('w')
    proc=None
    page=None
    try:
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT/'backend',env=dict(case_env),stdout=logfile,stderr=subprocess.STDOUT)
        verify_server(proc,base);page=browser.new_page(viewport={'width':1400,'height':950});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def get():return page.request.get(base+'/api/flow/state').json()
        def post(path,body=None):
            r=page.request.post(base+path,data=body);assert r.ok,r.text();return r.json()
        def refresh(phase):
            page.reload(wait_until='networkidle')
            selector={'reading':'.reading-chunk-body','review':'md-filled-tonal-button:has-text("显示答案")','complete':'.round-summary'}[phase]
            page.wait_for_selector(selector)
            assert get()['flow']['phase']==phase
        def act(selector):
            with page.expect_response(lambda r:'/api/flow/state' in r.url and r.request.method=='GET') as response:
                page.locator(selector).click()
            return response.value.json()
        page.goto(base,wait_until='networkidle')
        if kind not in ('legacy','interrupted'):
            page.wait_for_selector('.screen-title')
            d=act('md-filled-button:has-text("开始")')
        else:d=get();assert d['flow']['legacy']==(kind=='legacy')
        if kind=='exit':
            act('md-text-button:has-text("退出本轮")')
            assert get()['flow'] is None
            page.wait_for_selector('.screen-title')
            assert post('/api/flow/start')['flow']['phase']=='reading'
            return
        if kind=='end':
            d=act('md-text-button:has-text("结束阅读")')
            assert d['flow']['phase']=='review' and d['flow']['stats']['readingSkipped']==s
        seq=[];did_undo=False;did_trace=False;did_split=False
        while d['flow']['phase']!='complete':
            phase=d['flow']['phase'];refresh(phase)
            if phase=='reading':
                if kind=='edges' and not did_split:
                    page.wait_for_selector('.reading-chunk-body .line-handle')
                    assert page.locator('md-filter-chip[label="书签模式"], md-filter-chip[label="提炼模式"], .gap-policy-row').count()==0
                    # Exercise the existing line-handle split control in the UI.
                    page.locator('.reading-chunk-body .line-handle[data-line-start="3"]').click()
                    with page.expect_response(lambda r:'/api/reading/split' in r.url):
                        page.locator('.split-confirm__ok').click()
                    refresh('reading');assert get()['flow']['reading_consumed']==0
                    did_split=True
                seq.append('R');d=act('md-text-button:has-text("下一张（稍后继续）")')
            else:
                if s and not did_trace and d['cards'][0].get('noteId')==5000:
                    page.wait_for_selector('md-icon-button[data-aria-label="溯源：回到制卡时的原文片段"]')
                    button=page.locator('md-icon-button[data-aria-label="溯源：回到制卡时的原文片段"]')
                    page.wait_for_function("() => !document.querySelector('md-icon-button[data-aria-label=\"溯源：回到制卡时的原文片段\"]').disabled")
                    assert button.evaluate('(element) => !element.disabled')
                    before=get()['flow'];button.click();page.wait_for_selector('.reading-chunk-body')
                    assert page.locator('md-filter-chip[label="书签模式"], md-filter-chip[label="提炼模式"], .gap-policy-row').count()==0
                    page.keyboard.press('Escape');page.wait_for_selector('md-filled-tonal-button:has-text("显示答案")')
                    assert get()['flow']==before;did_trace=True
                seq.append('C')
                page.locator('md-filled-tonal-button:has-text("显示答案")').click()
                d=act('md-filled-button.ease-good')
                if d['flow']['phase']=='reading' and not did_undo:
                    reading_count=d['flow']['reading_consumed'];new_count=d['flow']['stats']['newReviewed']
                    d=act('md-text-button:has-text("撤销上次评分")')
                    assert d['flow']['phase']=='review' and d['flow']['reading_consumed']==reading_count
                    assert d['flow']['stats']['newReviewed']==new_count-1
                    refresh('review');page.locator('md-filled-tonal-button:has-text("显示答案")').click()
                    d=act('md-filled-button.ease-good');assert d['flow']['phase']=='reading';did_undo=True
                if kind=='edges' and d['flow']['phase']=='reading':
                    post('/api/reading/list/remove',{'path':d['reading_round']['chunks'][0]['path']})
                    page.reload(wait_until='networkidle');d=get()
        if kind not in ('edges','end'):
            expected=[]
            if s:
                for i in range(1,s+1):expected+=['R']+['C']*((i*c)//s-((i-1)*c)//s)
            else:expected=['C']*c
            assert seq==expected,(seq,expected)
        page.wait_for_selector('.round-summary');refresh('complete')
        assert d['flow']['stats']['reviewed']==c and d['flow']['stats']['newReviewed']==c
        if s and c:assert did_trace,'trace detour was never exercised'
        page.screenshot(path=str(RUN/f'ui-{s}-{c}-{kind}.png'))
        assert not errors,errors
        # Explicit exit clears both queues; end-reading continues cards in another flow.
        post('/api/flow/exit');assert get()['flow'] is None
        print(f'UI passed: {s} readings/{c} cards {kind}',flush=True)
    finally:
        try:
            if page:page.close()
        finally:
            try:
                if proc:
                    proc.terminate()
                    try:proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
            finally:
                logfile.close()

def main():
    # Fail at the normal loopback operation if sandbox disallows it; no bypass.
    reserve_loopback_port()
    for s,c,kind in [(3,8,'normal'),(7,1,'normal'),(0,3,'normal'),(3,0,'normal'),(0,0,'normal'),(1,1,'normal'),(2,4,'edges'),(2,4,'legacy'),(2,4,'interrupted'),(3,4,'end'),(2,4,'exit')]:
        try:
            case_env=prepare(s,c,kind)
            with sync_playwright() as p:
                browser=p.chromium.launch()
                try:run_case(browser,s,c,kind,case_env)
                finally:browser.close()
        finally:
            # Only after uvicorn stops and Playwright's event loop exits.
            # Also covers failed setup, browser launch or server creation.
            cleanup()
if __name__=='__main__':main()
