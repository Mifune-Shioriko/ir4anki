"""Two real editors, state response races, normal native study; isolated dist only."""
import os, sys, json, subprocess, signal
from pathlib import Path
from native_fixture import configure, NativeData, cleanup, reserve_loopback_port, verify_server
from pylib_test_support import run_alive
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
DIST=Path(os.environ['REGRESSION_DIST']).resolve()
assert DIST != ROOT/'frontend'/'dist' and (DIST/'index.html').is_file()
for k in list(os.environ):
    if k.startswith('ANKI_') or k=='ANKICONNECT_URL':os.environ.pop(k)
os.environ.update(ANKI_READING_MODE='1',ANKI_PREVIEW_MODE='0',REVIEW_DIST_DIR=str(DIST))
configure();sys.path.insert(0,str(ROOT/'backend'))
import app as b
async def seed():
    try:
        native=NativeData(b)
        for i in range(3):native.add_card(730000+i,deck='2026',note=830000+i)
        (b.NOTES_DIR/'note.md').write_text('# Audit\n\n'+('Long reading content. ' * 20))
        await b.reading_list_add(b.ReadingPathBody(path='note.md'))
        await b.flow_start()
    finally:await b._pylib.close()
proc=None
try:
    run_alive(seed())
    port=reserve_loopback_port();base=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT/'backend',env=os.environ.copy())
    verify_server(proc,base)
    with sync_playwright() as p:
        browser=p.chromium.launch()
        import base64
        from urllib.parse import quote
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF1sAAAAASUVORK5CYII=')
        image=browser.new_page(viewport={'width':1600,'height':1000})
        image_names=['x (1).png','中文 图.png','percent%.png','delimiters?#.png','ordinary.png']
        for name in image_names:(b.NOTES_DIR/name).write_bytes(png)
        note='\n'.join('![image]('+quote(name,safe='')+'?size=2&v=%25#part)' for name in image_names)
        files=[('a/'+name,png) for name in image_names]+[('a/upload.md',note.encode())]
        # Build multipart bytes explicitly to preserve repeated field names.
        boundary='audit-disposable-multipart';body=bytearray()
        for path,data in files:
            body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="rel_paths"\r\n\r\n'+path+'\r\n').encode())
            body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="files"; filename="fixture"\r\nContent-Type: application/octet-stream\r\n\r\n').encode());body.extend(data);body.extend(b'\r\n')
        body.extend(('--'+boundary+'--\r\n').encode())
        uploaded=image.request.post(base+'/api/files/upload',data=bytes(body),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        assert uploaded.status==200,uploaded.text()
        image_responses=[]
        image.on('response',lambda response:image_responses.append((response.url,response.status)) if '/api/reading/media/' in response.url else None)
        image.goto(base);image.get_by_role('navigation',name='主导航').get_by_role('button',name='文件',exact=True).click()
        image.get_by_role('treeitem',name='a',exact=True).click()
        image.get_by_role('treeitem',name='a/upload.md',exact=True).click()
        image.wait_for_function("() => { const imgs=[...document.querySelectorAll('.files-viewer-body img')]; return imgs.length===5 && imgs.every(i => i.complete && i.naturalWidth > 0) }")
        assert len(image_responses)==5 and all(status==200 for _,status in image_responses),image_responses
        assert image.locator('.files-viewer-body img').evaluate_all("imgs => imgs.every(i => i.getAttribute('src').endsWith('?size=2&v=%25#part'))")
        image.close()
        a=browser.new_page(viewport={'width':1600,'height':1000});other=browser.new_page(viewport={'width':1600,'height':1000})
        requests=[]
        for page in (a,other):
            page.on('request',lambda req: requests.append(req.post_data_json) if '/api/reading/edit' in req.url else None)
            page.goto(base);page.wait_for_selector('.reading-chunk-body')
            page.locator('md-icon-button[data-aria-label="编辑片段"]').click()
            page.wait_for_selector('.seg-edit-dialog .cm-content')
        for page,marker in [(a,'EDITOR_A'),(other,'EDITOR_B_DRAFT')]:
            page.locator('.seg-edit-dialog .cm-content').click();page.keyboard.press('Control+End');page.keyboard.type(' '+marker)
        with a.expect_response(lambda r:'/api/reading/edit' in r.url) as response:
            a.locator('.seg-edit-dialog md-filled-button').filter(has_text='保存').click()
        assert response.value.status==200
        a.wait_for_selector('.seg-edit-dialog',state='detached')
        with other.expect_response(lambda r:'/api/reading/edit' in r.url) as response:
            other.locator('.seg-edit-dialog md-filled-button').filter(has_text='保存').click()
        assert response.value.status==409
        other.wait_for_selector('.seg-edit-dialog .edit-error')
        assert '草稿已保留' in other.locator('.edit-error').inner_text()
        assert 'EDITOR_B_DRAFT' in other.locator('.seg-edit-dialog .cm-content').inner_text()
        assert len(requests)==2 and requests[0]['expected_revision']==requests[1]['expected_revision']
        assert 'EDITOR_A' in (b.NOTES_DIR/'note.md').read_text() and 'EDITOR_B_DRAFT' not in (b.NOTES_DIR/'note.md').read_text()
        other.close()
        # Complete reading through the API, then normal UI study; never sync.
        state=a.request.get(base+'/api/flow/state').json();chunk=state['reading_round']['chunks'][0]
        result=a.request.post(base+'/api/reading/act',params={'path':chunk['path'],'chunk_key':chunk['chunk_key'],'action':'complete'})
        assert result.status==200
        a.reload();a.wait_for_selector('md-filled-tonal-button:has-text("显示答案")')
        def wire():return a.request.get(base+'/api/flow/state').json()
        before=wire();first=before['cards'][0]['cardId']
        # Navigation launches GET but leaves the currently presented review UI.
        held=[]
        a.route('**/api/flow/state',lambda route:held.append(route))
        a.get_by_role('navigation',name='主导航').get_by_role('button',name='文件',exact=True).click()
        a.get_by_role('navigation',name='主导航').get_by_role('button',name='学习',exact=True).click()
        a.wait_for_timeout(100);assert held
        a.locator('md-filled-tonal-button:has-text("显示答案")').click()
        with a.expect_response(lambda r:'/api/answer?' in r.url) as response:a.locator('md-filled-button.ease-good').click()
        assert response.value.status==200
        after=response.value.json()['state'];assert after['cards'][0]['cardId']!=first
        for route in held:route.fulfill(status=200,content_type='application/json',body=json.dumps(before))
        held.clear();a.wait_for_timeout(100)
        # Response observers confirm the UI still grades the newer card.
        a.locator('md-filled-tonal-button:has-text("显示答案")').click()
        with a.expect_request(lambda r:'/api/answer?' in r.url) as req:a.locator('md-filled-button.ease-good').click()
        assert f"card_id={after['cards'][0]['cardId']}" in req.value.url
        # A stale failed GET cannot replace a successful newer recovery GET.
        a.get_by_role('navigation',name='主导航').get_by_role('button',name='文件',exact=True).click()
        a.get_by_role('navigation',name='主导航').get_by_role('button',name='学习',exact=True).click()
        a.wait_for_timeout(100);assert held
        old=held.pop(0)
        a.get_by_role('navigation',name='主导航').get_by_role('button',name='文件',exact=True).click()
        a.get_by_role('navigation',name='主导航').get_by_role('button',name='学习',exact=True).click()
        a.wait_for_timeout(100);assert held
        for route in held:route.fulfill(status=200,content_type='application/json',body=json.dumps(wire()))
        old.fulfill(status=500,content_type='application/json',body='{"detail":"stale failure"}')
        a.wait_for_timeout(100)
        assert '加载失败，请重试' not in a.locator('body').inner_text()
        browser.close()
        print('PASS actual uploaded browser images including encoded filenames, two editor conflict/draft, normal native study, GET vs answer, stale GET failure/recovery')
finally:
    if proc is not None:
        proc.send_signal(signal.SIGTERM)
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
    cleanup()
