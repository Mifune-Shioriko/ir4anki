"""Mocked browser APIs only, every network request intercepted; no server."""
import argparse, json, mimetypes, subprocess
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True);dist=a.output/'fixture-dist'
script="""import {build} from 'vite';import solid from 'vite-plugin-solid';
await build({configFile:false,root:'tests',plugins:[solid()],build:{outDir:process.argv[1],emptyOutDir:true,rollupOptions:{input:'tests/manual-sync.html'}}});"""
subprocess.run(['node','--input-type=module','-e',script,str(dist)],cwd=ROOT/'frontend',check=True)
with sync_playwright() as pw:
    try: browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
    except Exception as error:
        (a.output/'launch-failure.json').write_text(json.dumps({'stage':'chromium-launch','error':str(error)},indent=2));raise
    for width,theme in [(1440,'light'),(390,'dark')]:
        page=browser.new_page(viewport={'width':width,'height':900},color_scheme=theme)
        calls=[];pending=[];mode=['hold']
        def route(r):
            path=urlparse(r.request.url).path
            if path=='/api/engine/status':
                assert r.request.method=='GET'
                r.fulfill(json={'backend':'pylib','last_sync_success':'fixture','local_changes_pending':True});return
            if path=='/api/local/sync':
                assert r.request.method=='POST'
                assert r.request.post_data_json=={'commit_undo':True}
                assert r.request.headers['x-ir4anki-local-sync']=='confirm'
                assert 'authorization' not in r.request.headers
                calls.append(r.request.post_data_json)
                if mode[0]=='hold':pending.append(r)
                else:r.fulfill(status=409,json={'detail':'Anki requires full sync; resolve upload/download in desktop Anki, never chosen automatically'})
                return
            assert not path.startswith('/api/'),path
            f=dist/path.lstrip('/');assert f.is_file(),path
            r.fulfill(body=f.read_bytes(),content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')
        page.route('**/*',route);page.goto('http://127.0.0.1:8901/manual-sync.html')
        button=page.get_by_role('button',name='手动同步',exact=True)
        expect(button).to_have_attribute('aria-haspopup','dialog')
        assert button.locator('.nav-rail__indicator svg').count()==1
        button.focus();assert button.evaluate('(e)=>e.matches(":focus-visible")')
        button.click();expect(page.locator('md-dialog')).to_be_visible()
        assert page.locator('input[type=password]').count()==0
        expect(page.get_by_role('button',name='确认并同步')).to_be_disabled()
        assert not calls
        page.get_by_role('button',name='取消').click();expect(page.locator('md-dialog')).to_have_count(0);assert not calls
        button.click();page.get_by_role('checkbox').check()
        expect(page.get_by_role('checkbox')).to_have_accessible_name('确认结束最后一次评分的撤销，即使同步失败也不能恢复。')
        confirm=page.get_by_role('button',name='确认并同步');confirm.click()
        expect(page.get_by_role('button',name='正在同步…')).to_be_disabled()
        expect(page.get_by_role('button',name='取消')).to_be_disabled()
        # Dispatch a second event to exercise the handler's synchronous busy guard.
        page.locator('md-filled-button').dispatch_event('click')
        page.wait_for_timeout(100);assert len(calls)==1 and len(pending)==1
        pending.pop().fulfill(json={'synced':True})
        expect(page.get_by_role('status').last).to_contain_text('集合与媒体同步完成')
        expect(page.get_by_role('button',name='确认并同步')).to_be_disabled()
        page.get_by_role('checkbox').check();mode[0]='error'
        page.get_by_role('button',name='确认并同步').click()
        expect(page.get_by_role('status').last).to_contain_text('同步失败')
        expect(page.get_by_role('status').last).to_contain_text('full sync')
        assert len(calls)==2
        expect(page.get_by_role('button',name='取消')).to_be_enabled()
        page.screenshot(path=str(a.output/f'{width}-{theme}.png'))
        page.get_by_role('button',name='取消').click();assert len(calls)==2
        page.close()
    browser.close()
print('PASS: mock-only manual sync confirmation, cancel, duplicate guard, loading, success, error and themes')
