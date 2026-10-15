"""Synthetic browser fixture: every request intercepted; no services or real data."""
import argparse, json, mimetypes, traceback, time, re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

p = argparse.ArgumentParser()
p.add_argument('--run', type=Path, required=True)
p.add_argument('--fail-fast', action='store_true', help='Keep the full planned matrix; stop after the first failure')
a = p.parse_args()
run = a.run.resolve()
out = run / 'browser'
out.mkdir(exist_ok=True)
dist = run / 'fixture-dist'
records = []
(out/'planned.json').write_text(json.dumps({'matrix':[{'theme':t,'width':w,'reduced_motion':True,'zoom':[100,200]} for t in ['light','dark'] for w in [360,640,900,1280,1920]],'checks':['menu/context/F2','long mixed names','empty ancestry search','refresh preservation','prefix relocation','undo','drag root/cancel/conflict/self/descendant/same-parent/hover/autoscroll','retry','resize','cache warm','shared-consumer cancellation','A/B/A stale response','unmount','light/dark','zoom/overflow','F1 response-loss/fs/db/flow reconciliation','F2 historical undo rejection + unchanged retry','F3 blur/visibility/lostcapture/buttons/outside cancellation','F4 actual App delayed current source unrelated/matching relocation']},indent=2))

def save():
    (out / 'ledger.json').write_text(json.dumps(dict(cases=records, passed=sum(r['status']=='PASS' for r in records), failed=sum(r['status']=='FAIL' for r in records)), ensure_ascii=False, indent=2))
    if records:
        (out / f'case-{len(records):03}.json').write_text(json.dumps(records[-1], ensure_ascii=False, indent=2))

long = '中文文件名称与可读性验证'*8 + '😀Englishאבג.md'
initial = {'甲/A.md': '# A\n\nA synthetic $x^2$\n\n' + 'paragraph\n\n'*200, '甲/B.md': '# B\n\nB synthetic', '乙/同名.md': '# duplicate', '甲/'+long: '# 长名\n\n正文'}
initial.update({f'长树/{i:03}.md': f'# synthetic {i}' for i in range(100)})

def css_string(value):
    # JSON's \\uNNNN is not a CSS Unicode escape. Quote/escape CSS strings
    # directly, preserving Chinese/non-BMP names and escaping controls safely.
    return '"' + ''.join(('\\'+format(ord(c),'x')+' ') if ord(c)<32 or ord(c)==127 else ('\\'+c) if c in '\\"' else c for c in value) + '"'

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(headless=True, chromium_sandbox=True)
    except Exception as e:
        (out / 'launch-error.json').write_text(json.dumps(dict(error=str(e), sandbox=True), indent=2))
        raise
    for theme in ['light', 'dark']:
      for width in [360, 640, 900, 1280, 1920]:
        case = dict(theme=theme, width=width, status='RUNNING', errors=[], calls=[], timings={}, diagnostics={})
        records.append(case)
        save()
        page = browser.new_page(viewport=dict(width=width, height=900), color_scheme=theme, reduced_motion='reduce')
        page.on('pageerror', lambda e: case['errors'].append(str(e)))
        corpus = dict(initial)
        dirs = ['甲', '乙', '空', '空/子', '长树']
        ops = {}
        revision = [1]
        fail_raw = set()
        requests = []
        results = {}
        fault_mode = [None]
        status_down = [0]
        pending_recovery = {}
        versions, undos = {}, {}

        def state_at(path):
            files = sorted((key[len(path):], text) for key,text in corpus.items() if key==path or key.startswith(path+'/'))
            folders = sorted(d[len(path):] for d in dirs if d==path or d.startswith(path+'/'))
            return (files,folders) if files or folders else None

        def relocate(old, new):
            for key in list(corpus):
                if key == old or key.startswith(old+'/'):
                    corpus[new+key[len(old):]] = corpus.pop(key)
            dirs[:] = [new+d[len(old):] if d==old or d.startswith(old+'/') else d for d in dirs]
            revision[0] += 1

        def route(r):
            u = urlparse(r.request.url)
            path = u.path
            case['calls'].append(dict(method=r.request.method, path=path, query=u.query))
            assert u.hostname == 'files.fixture', 'Rejected network ' + r.request.url
            if path.startswith('/api/'):
                status, body = 200, {}
                if path == '/api/files/list':
                    body = dict(files=[dict(path=k, title=Path(k).stem, revision=str(revision[0])) for k in corpus], dirs=dirs)
                elif path == '/api/files/membership':
                    body = dict(paths=['甲/A.md'])
                elif path == '/api/files/operation':
                    request_id = parse_qs(u.query)['request_id'][0]
                    if status_down[0]:
                        status_down[0] -= 1
                        r.abort('failed'); return
                    if request_id in pending_recovery:
                        relocate(*pending_recovery.pop(request_id))
                        result = results[request_id]
                        versions[result['operationId']] = state_at(result['path'])
                    body = results.get(request_id, dict(status='not_found'))
                    if body.get('status')=='complete' and (state_at(body['old_path']) is not None or state_at(body['path'])!=versions[body['operationId']]):
                        status,body=409,dict(detail='操作曾完成，但当前状态已变化')
                elif path == '/api/files/raw':
                    target = parse_qs(u.query)['path'][0]
                    requests.append(target)
                    if target in fail_raw:
                        status, body = 500, dict(detail='synthetic read failure')
                    elif target not in corpus:
                        status, body = 404, dict(detail='synthetic missing')
                    else:
                        body = dict(path=target, text=corpus[target], revision=str(revision[0]))
                elif path in ['/api/files/rename', '/api/files/move', '/api/files/undo']:
                    payload = r.request.post_data_json
                    if fault_mode[0]=='not-delivered':
                        fault_mode[0]=None;r.abort('failed');return
                    if path.endswith('/undo'):
                        original = payload['operationId']
                        if original in undos:
                            previous = undos[original]
                            if state_at(previous['old_path']) is not None or state_at(previous['path'])!=versions[previous['operationId']]:
                                status,body=409,dict(detail='撤销曾完成，但当前状态已变化')
                            else:
                                body=previous
                                if payload.get('requestId'): results[payload['requestId']]=dict(body,status='complete')
                            r.fulfill(status=status,content_type='application/json',body=json.dumps(body));return
                        new, old = ops[original]
                        if state_at(old)!=versions[original]:
                            r.fulfill(status=409,content_type='application/json',body=json.dumps(dict(detail='文件已变化，不能撤销')));return
                    else:
                        old = payload['path']
                        name = Path(old).name
                        if path.endswith('/rename'):
                            name = payload['new_name'] + ('' if old in dirs or payload['new_name'].endswith('.md') else '.md')
                            parent = str(Path(old).parent)
                            parent = '' if parent == '.' else parent
                        else:
                            parent = payload['new_dir']
                        new = (parent+'/' if parent else '') + name
                    if new in corpus or new in dirs:
                        status, body = 409, dict(detail='目标已存在（合成冲突）')
                    else:
                        op = str(len(ops)+1)
                        ops[op] = (old, new)
                        body = dict(ok=True, path=new, old_path=old, operationId=op)
                        request_id = payload.get('requestId')
                        if request_id: results[request_id] = dict(body, status='complete')
                        if fault_mode[0] in ['fs', 'db', 'flow']:
                            pending_recovery[request_id] = (old, new)
                            fault_mode[0] = None
                            status, body = 503, dict(detail='合成迁移待恢复')
                        else:
                            relocate(old, new)
                            versions[op] = state_at(new)
                            if path.endswith('/undo'): undos[payload['operationId']] = body
                            if fault_mode[0] == 'lost-undo':
                                fault_mode[0] = None
                                r.abort('failed'); return
                            if fault_mode[0] == 'lost-response':
                                fault_mode[0] = None
                                r.abort('failed'); return
                elif path == '/api/files/delete':
                    old = r.request.post_data_json['path']
                    for key in list(corpus):
                        if key==old or key.startswith(old+'/'):
                            del corpus[key]
                    dirs[:] = [d for d in dirs if d!=old and not d.startswith(old+'/')]
                    body = dict(ok=True)
                else:
                    status, body = 500, dict(detail='unexpected synthetic API')
                r.fulfill(status=status, content_type='application/json', body=json.dumps(body))
                return
            f = (dist / ('files-fixture.html' if path=='/' else path.lstrip('/'))).resolve()
            assert f.is_relative_to(dist) and f.is_file(), f
            r.fulfill(body=f.read_bytes(), content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')

        page.route('**/*', route)
        def item(path):
            return page.locator('[role=treeitem]').and_(page.locator('[data-path='+css_string(path)+']'))
        def open_dir(path):
            if item(path).get_attribute('aria-expanded') != 'true':
                item(path).locator('.files-tree-chevron').click()
        def back():
            if width <= 900:
                page.get_by_text('返回文件列表', exact=True).click()
        def menu(path, label, keyboard=False):
            # Operate the actual Material interactive root, not a custom host's
            # bounding box. Record closure reason/focus/busy if selection fails.
            trace = len(case['diagnostics'].setdefault('menu_interactions', []))
            page.evaluate("window.menuTrace=[];window.menuCapture=e=>{if(['pointerdown','pointerup','gotpointercapture','lostpointercapture','fixture-files-command','close-menu'].includes(e.type)||e.composedPath().some(n=>n.localName==='md-menu-item'))window.menuTrace.push({type:e.type,targetPath:e.target?.dataset?.path||null,reason:e.detail?.reason,command:e.type==='fixture-files-command'?e.detail:undefined,path:e.composedPath().map(n=>n.localName||n.constructor.name)})};window.addEventListener('close-menu',window.menuCapture,true);window.addEventListener('click',window.menuCapture,true);window.addEventListener('fixture-files-command',window.menuCapture,true);['pointerdown','pointerup','gotpointercapture','lostpointercapture'].forEach(t=>window.addEventListener(t,window.menuCapture,true))")
            trigger = item(path).locator('md-icon-button')
            before = trigger.evaluate('e=>({disabled:e.disabled,expanded:e.getAttribute("aria-expanded")})')
            if keyboard:
                trigger.locator('button').focus();trigger.locator('button').press('Enter')
            else:
                trigger.click()
            opened = item(path).locator('md-menu[open]')
            selected = opened.get_by_role('menuitem', name=re.compile(re.escape(label)))
            selected.wait_for(state='visible')
            page.screenshot(path=str(out/f'{theme}-{width}-menu-{trace:02}.png'))
            if keyboard:
                selected.focus();selected.press('Enter')
            else:
                selected.click()
            diag=dict(path=path,label=label,keyboard=keyboard,before=before,
                      events=page.evaluate('window.menuTrace'),
                      after=page.evaluate('({dialogs:[...document.querySelectorAll("md-dialog")].map(d=>({open:d.open,text:d.textContent})),pending:sessionStorage.getItem("files-pending-relocation-v1"),active:document.activeElement?.localName,snack:document.querySelector(".snackbar")?.textContent})'))
            case['diagnostics']['menu_interactions'].append(diag);save()
            assert not any(e['type']=='gotpointercapture' and e.get('targetPath')==path for e in diag['events']), diag
            expected='keydown' if keyboard else 'click-selection'
            assert any(e['type']=='close-menu' and e.get('reason',{}).get('kind')==expected for e in diag['events']), diag
            page.evaluate("window.removeEventListener('close-menu',window.menuCapture,true);window.removeEventListener('click',window.menuCapture,true);window.removeEventListener('fixture-files-command',window.menuCapture,true);['pointerdown','pointerup','gotpointercapture','lostpointercapture'].forEach(t=>window.removeEventListener(t,window.menuCapture,true))")
            if label in ['移动到','重命名','永久删除']:
                page.locator('md-dialog[open]').wait_for(state='visible',timeout=5000)

        try:
            start = time.perf_counter()
            page.goto('http://files.fixture/')
            page.wait_for_selector('[data-path="空"]')
            case['timings']['initial_ms'] = (time.perf_counter()-start)*1000
            assert page.locator('.files-row-actions').count()==0
            open_dir('甲')
            # Native menu selections; clipboard is capability-controlled with
            # synthetic writes only. Never access the OS clipboard.
            page.evaluate("window.originalClipboardDescriptor=Object.getOwnPropertyDescriptor(navigator,'clipboard')")
            for mode in ['unavailable','rejected','success']:
                page.evaluate("mode=>{window.copyCalls=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:mode==='unavailable'?undefined:{writeText:path=>{window.copyCalls.push(path);return mode==='rejected'?Promise.reject(new Error('synthetic denied')):Promise.resolve()}}})}",mode)
                menu('甲/A.md','复制路径',keyboard=(mode=='rejected'))
                if mode=='success':
                    page.wait_for_function("document.querySelector('.snackbar')?.textContent.includes('路径已复制')")
                else:
                    page.wait_for_function("document.querySelector('.snackbar')?.textContent.includes('手动复制')")
                    assert '甲/A.md' in page.locator('.snackbar').inner_text()
                assert page.locator('.snackbar').get_attribute('role')=='status'
                assert page.evaluate('window.copyCalls')==([] if mode=='unavailable' else ['甲/A.md'])
                assert not case['errors'],case['errors']
                case['diagnostics']['clipboard-'+mode]='PASS';save()
            page.evaluate("()=>{if(window.originalClipboardDescriptor)Object.defineProperty(navigator,'clipboard',window.originalClipboardDescriptor);else delete navigator.clipboard;delete window.originalClipboardDescriptor}")
            start = time.perf_counter()
            item('甲/A.md').click()
            page.wait_for_selector('[data-file-path="甲/A.md"] h1')
            case['timings']['cold_ms'] = (time.perf_counter()-start)*1000
            assert page.locator('.files-preview-heading').get_by_text('重命名', exact=True).count()==0
            back()
            item('甲/B.md').click()
            page.wait_for_selector('[data-file-path="甲/B.md"] h1')
            back()
            count = len(requests)
            start = time.perf_counter()
            item('甲/A.md').click()
            page.wait_for_selector('[data-file-path="甲/A.md"] h1')
            case['timings']['warm_ms'] = (time.perf_counter()-start)*1000
            assert len(requests)==count
            back()
            assert item('甲/'+long).get_attribute('title')=='甲/'+long
            glyph = item('甲/A.md').locator('md-icon-button').evaluate('e=>({host:e.getBoundingClientRect().width,button:e.shadowRoot.querySelector("button").getBoundingClientRect().width,icon:e.querySelector("svg").getBoundingClientRect().width})')
            case['diagnostics']['action_dimensions'] = glyph
            assert glyph==dict(host=40,button=40,icon=20), glyph
            search = page.get_by_role('searchbox')
            search.fill('空/子')
            assert item('空').count()==1 and item('空/子').count()==1
            search.fill('')
            item('甲').locator('.files-tree-chevron').click()
            page.get_by_label('刷新', exact=True).click()
            page.wait_for_timeout(100)
            assert item('甲/A.md').count()==0
            open_dir('甲')
            item('甲/A.md').focus()
            page.keyboard.press('Shift+F10')
            page.keyboard.press('Escape')
            item('甲/A.md').focus()
            page.keyboard.press('F2')
            page.wait_for_selector('.files-rename-input')
            textbox = page.locator('.files-rename-input input')
            textbox.focus()
            textbox.fill('新A')
            case['diagnostics']['rename_focus'] = textbox.evaluate('e=>({focused:e.getRootNode().activeElement===e,value:e.value})')
            textbox.evaluate("e=>e.addEventListener('keydown',event=>{window.renameKeyPath=event.composedPath().map(n=>n.localName||n.constructor.name);if(event.key==='Enter'&&!event.repeat){e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',repeat:true,bubbles:true,composed:true}))}},{once:true})")
            textbox.press('Enter')
            case['diagnostics']['rename_key_path'] = page.evaluate('window.renameKeyPath')
            page.wait_for_selector('[data-path="甲/新A.md"]')
            assert sum(c['path']=='/api/files/rename' for c in case['calls'])==1
            menu('甲', '移动到')
            page.locator('.files-move-dir').filter(has_text='乙').click()
            page.locator('md-dialog md-filled-button').filter(has_text='移动').click()
            page.wait_for_selector('[data-path="甲"]', state='detached')
            open_dir('乙')
            page.wait_for_selector('[data-path="乙/甲"]')
            assert '乙/甲' in page.locator('.files-current-dir').inner_text()
            open_dir('乙')
            open_dir('乙/甲')
            item('乙/甲/新A.md').click()
            page.wait_for_selector('[data-file-path="乙/甲/新A.md"] h1')
            back()
            page.get_by_text('撤销上次文件迁移', exact=True).click()
            page.wait_for_selector('[data-path="甲"]')
            open_dir('甲')
            # Actual pointer move and Escape cancellation.
            s, d = item('甲').bounding_box(), item('空').bounding_box()
            page.mouse.move(s['x']+75, s['y']+20)
            page.mouse.down()
            page.mouse.move(d['x']+70, d['y']+20, steps=8)
            assert page.locator('.files-drop-valid').count()==1
            page.keyboard.press('Escape')
            page.mouse.up()
            assert page.locator('.files-drop-valid').count()==0
            # Hover expansion, invalid same-parent/self/descendant feedback.
            s, d = item('甲/新A.md').bounding_box(), item('空').bounding_box()
            page.mouse.move(s['x']+75,s['y']+20);page.mouse.down();page.mouse.move(d['x']+70,d['y']+20,steps=8)
            page.wait_for_selector('[data-path="空/子"]')
            page.keyboard.press('Escape');page.mouse.up()
            for source_path, target_path in [('甲','甲'),('空','空/子'),('甲/新A.md','甲')]:
                s,d=item(source_path).bounding_box(),item(target_path).bounding_box()
                page.mouse.move(s['x']+75,s['y']+20);page.mouse.down();page.mouse.move(d['x']+70,d['y']+20,steps=8)
                if source_path==target_path:page.mouse.move(d['x']+90,d['y']+20,steps=4)
                assert page.locator('.files-drop-invalid').count()==1
                page.keyboard.press('Escape');page.mouse.up()
            # Edge autoscroll moves the real scroll container and keeps target feedback current.
            open_dir('长树')
            item('甲/新A.md').scroll_into_view_if_needed()
            s=item('甲/新A.md').bounding_box();bounds=page.locator('.files-tree').bounding_box()
            page.mouse.move(s['x']+75,s['y']+20);page.mouse.down();page.mouse.move(bounds['x']+90,bounds['y']+bounds['height']-8,steps=8)
            before_scroll=page.locator('.files-tree').evaluate('e=>e.scrollTop')
            page.wait_for_timeout(250)
            assert page.locator('.files-tree').evaluate('e=>e.scrollTop')>before_scroll
            page.keyboard.press('Escape');page.mouse.up()
            item('').scroll_into_view_if_needed()
            # Same-name conflict returns an error and preserves original membership.
            corpus['甲/同名.md']='# conflict target'
            page.get_by_label('刷新',exact=True).click()
            open_dir('乙');open_dir('甲')
            s,d=item('乙/同名.md').bounding_box(),item('甲').bounding_box()
            page.mouse.move(s['x']+75,s['y']+20);page.mouse.down();page.mouse.move(d['x']+70,d['y']+20,steps=8);page.mouse.up()
            page.wait_for_function("document.querySelector('.snackbar')?.textContent.includes('无法移动')")
            assert '无法移动' in page.locator('.snackbar').inner_text()
            assert '乙/同名.md' in corpus and '甲/同名.md' in corpus
            item('甲/新A.md').scroll_into_view_if_needed()
            mode_before_drop=page.locator('.files-screen').evaluate("e=>e.classList.contains('files-screen--preview')")
            assert page.locator('.files-tree-card').is_visible()
            s, d = item('甲/新A.md').bounding_box(), item('').bounding_box()
            page.mouse.move(s['x']+75,s['y']+20)
            page.mouse.down()
            page.mouse.move(d['x']+70,d['y']+20,steps=8)
            page.mouse.up()
            page.wait_for_selector('[data-path="新A.md"]')
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            assert page.locator('.files-screen').evaluate("e=>e.classList.contains('files-screen--preview')")==mode_before_drop
            assert page.locator('.files-tree-card').is_visible() and item('新A.md').is_visible()
            case['diagnostics']['drop-preserves-list-preview-mode']='PASS';save()
            page.get_by_text('撤销上次文件迁移', exact=True).click()
            page.wait_for_selector('[data-path="甲/新A.md"]')
            fail_raw.add('甲/B.md')
            page.evaluate("window.fixture.invalidateFileCache('甲/B.md')")
            item('甲/B.md').click()
            page.wait_for_selector('.file-viewer-error')
            fail_raw.clear()
            page.locator('.file-viewer-error md-text-button').click()
            page.wait_for_selector('[data-file-path="甲/B.md"] h1')
            back()
            # F3 lifecycle: each cancelled drag and its following ordinary click
            # must issue zero mutations; root movement above remains the positive case.
            for cancel in ['blur', 'visibilitychange', 'lostpointercapture', 'buttons=0', 'outside']:
                s,d=item('甲/B.md').bounding_box(),item('空').bounding_box()
                before_moves=sum(c['path']=='/api/files/move' for c in case['calls'])
                page.mouse.move(s['x']+75,s['y']+20);page.mouse.down();page.mouse.move(d['x']+70,d['y']+20,steps=8)
                if cancel=='buttons=0': page.evaluate("window.dispatchEvent(new PointerEvent('pointermove',{pointerId:1,buttons:0}))")
                elif cancel=='outside': page.mouse.move(-20,-20);page.mouse.up()
                elif cancel=='visibilitychange': page.evaluate("() => { Object.defineProperty(document,'hidden',{value:true,configurable:true});document.dispatchEvent(new Event('visibilitychange'));delete document.hidden }")
                else: page.evaluate('(name)=>window.dispatchEvent(new Event(name))', cancel)
                page.mouse.up();item('空').click()
                assert sum(c['path']=='/api/files/move' for c in case['calls'])==before_moves,cancel
                assert page.locator('.files-drop-valid,.files-drop-invalid').count()==0
                case['diagnostics']['F3-'+cancel]='PASS';save()
            # F1: actual browser fetch response loss and deferred FS/DB recovery.
            for phase in ['lost-response','fs','db','flow']:
                item('甲/B.md').click();page.wait_for_selector('[data-file-path="甲/B.md"] h1');back()
                page.evaluate("window.relocations=[];window.addEventListener('files-relocated',window.trackRelocation=e=>window.relocations.push(e.detail))")
                fault_mode[0]=phase;status_down[0]=1
                menu('甲','移动到', keyboard=(phase=='db'));page.locator('.files-move-dir').filter(has_text='空').click()
                page.locator('md-dialog md-filled-button').filter(has_text='移动').click()
                page.wait_for_function("document.querySelector('.snackbar')?.textContent.includes('待确认')")
                assert page.evaluate('window.relocations.length')==0
                assert page.evaluate("JSON.parse(sessionStorage.getItem('files-pending-relocation-v1')).path")=='甲'
                # Close the pending dialog so the refresh entry is accessible.
                page.locator('md-dialog md-text-button').filter(has_text='取消').click()
                page.get_by_label('刷新',exact=True).click()
                page.wait_for_function('window.relocations.length===1')
                assert page.evaluate('window.relocations[0]')==dict(oldPath='甲',newPath='空/甲')
                assert '空/甲' in page.locator('.files-current-dir').inner_text()
                assert page.locator('.files-preview-heading h2').get_attribute('title')=='空/甲/B.md'
                if width<=900:
                    open_dir('空');open_dir('空/甲');item('空/甲/B.md').click()
                page.wait_for_selector('[data-file-path="空/甲/B.md"] h1');back()
                page.get_by_label('刷新',exact=True).click();page.wait_for_timeout(50)
                assert page.evaluate('window.relocations.length')==1
                page.evaluate("window.removeEventListener('files-relocated',window.trackRelocation)")
                page.get_by_text('撤销上次文件迁移',exact=True).click();page.wait_for_selector('[data-path="甲"]')
                case['diagnostics']['F1-'+phase]='PASS';save()
            # R2-1 synthetic transport controls exercise real undo handler +
            # persisted refresh mapping; actual disk/DB/flow tests are separate.
            for phase in ['lost-undo','fs','db','flow']:
                menu('甲','移动到', keyboard=(phase=='db'));page.locator('.files-move-dir').filter(has_text='空').click()
                page.locator('md-dialog md-filled-button').filter(has_text='移动').click()
                page.wait_for_function("document.querySelector('.files-current-dir')?.textContent.includes('空/甲')")
                page.evaluate("window.relocations=[];window.addEventListener('files-relocated',window.undoTrack=e=>window.relocations.push(e.detail))")
                fault_mode[0]=phase;status_down[0]=1
                page.get_by_text('撤销上次文件迁移',exact=True).click()
                page.wait_for_function("JSON.parse(sessionStorage.getItem('files-pending-relocation-v1')||'null')?.action==='undo'")
                assert page.evaluate('window.relocations.length')==0
                page.get_by_label('刷新',exact=True).click()
                page.wait_for_function('window.relocations.length===1')
                assert page.evaluate('window.relocations[0]')==dict(oldPath='空/甲',newPath='甲')
                assert page.get_by_text('撤销上次文件迁移',exact=True).count()==0
                assert page.locator('.files-preview-heading h2').get_attribute('title')=='甲/B.md'
                page.evaluate("window.removeEventListener('files-relocated',window.undoTrack)")
                case['diagnostics']['R2-1-synthetic-'+phase]='PASS';save()
            # R2-2: an unknown persisted intent never acts on an external
            # same-path replacement, and unresolved operations disable mutations.
            fault_mode[0]='not-delivered'
            before=sum(c['path']=='/api/files/move' for c in case['calls'])
            menu('甲/B.md','移动到');page.locator('.files-move-dir').filter(has_text='空').click()
            page.locator('md-dialog md-filled-button').filter(has_text='移动').click()
            page.wait_for_function("document.querySelector('.snackbar')?.textContent.includes('待确认')")
            page.locator('md-dialog md-text-button').filter(has_text='取消').click()
            original_text=corpus['甲/B.md'];corpus['甲/B.md']='# replacement';revision[0]+=1
            page.get_by_label('刷新',exact=True).click()
            page.wait_for_function("document.body.textContent.includes('请求未执行')")
            assert sum(c['path']=='/api/files/move' for c in case['calls'])==before+1
            assert corpus['甲/B.md']=='# replacement' and '空/B.md' not in corpus
            assert page.evaluate("sessionStorage.getItem('files-pending-relocation-v1')") is None
            corpus['甲/B.md']=original_text;revision[0]+=1
            page.get_by_label('刷新',exact=True).click();page.wait_for_selector('[data-path="甲/B.md"]')
            case['diagnostics']['R2-2-unknown-intent-replacement-preserved']='PASS';save()
            # F2 UI guard: lost undo response, then another relocation. Historical
            # retry returns conflict and must not emit a stale canonical mapping.
            menu('甲','移动到');page.locator('.files-move-dir').filter(has_text='空').click()
            page.locator('md-dialog md-filled-button').filter(has_text='移动').click()
            page.wait_for_function("document.querySelector('.files-current-dir')?.textContent.includes('空/甲')")
            fault_mode[0]='lost-undo';status_down[0]=1
            page.get_by_text('撤销上次文件迁移',exact=True).click()
            page.wait_for_function("document.querySelector('.snackbar')?.textContent.includes('无法撤销')")
            relocate('甲','乙/甲')
            page.evaluate("window.relocations=[];window.addEventListener('files-relocated',window.trackRelocation=e=>window.relocations.push(e.detail))")
            page.get_by_label('刷新',exact=True).click()
            page.wait_for_function("document.querySelector('.files-load-error')?.textContent.includes('当前状态已变化') || document.body.textContent.includes('当前状态已变化')")
            assert page.evaluate('window.relocations.length')==0
            assert page.locator('.files-preview-heading h2').get_attribute('title')=='空/甲/B.md'
            # Restore only the fake route's state and prove unchanged retry is idempotent.
            relocate('乙/甲','甲')
            page.get_by_text('撤销上次文件迁移',exact=True).click()
            page.wait_for_function('window.relocations.length===1')
            assert page.evaluate('window.relocations[0]')==dict(oldPath='空/甲',newPath='甲')
            page.evaluate("window.removeEventListener('files-relocated',window.trackRelocation)")
            case['diagnostics']['F2-historical-undo-conflict-and-unchanged-retry']='PASS';save()
            if width > 900:
                handle = page.get_by_role('separator')
                box = handle.bounding_box()
                before = page.locator('.files-tree-card').bounding_box()['width']
                page.mouse.move(box['x']+4,box['y']+200)
                page.mouse.down()
                page.mouse.move(box['x']+84,box['y']+200)
                page.mouse.up()
                assert page.locator('.files-tree-card').bounding_box()['width'] > before
            overflow = page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
            case['diagnostics']['overflow'] = overflow
            assert overflow['scroll'] <= overflow['client'], overflow
            case['diagnostics']['dom_layout_ms'] = page.evaluate("performance.getEntriesByName('files-dom-layout').map(e=>e.duration)")
            case['diagnostics']['render_ms'] = page.evaluate("performance.getEntriesByName('files-markdown-render').map(e=>e.duration)")
            case['diagnostics']['dom_nodes'] = page.locator('*').count()
            page.screenshot(path=str(out / f'{theme}-{width}.png'), full_page=True)
            page.evaluate("document.documentElement.style.zoom='2'")
            page.screenshot(path=str(out / f'{theme}-{width}-zoom200.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth')
            # Actual shared FileViewer consumers with controllable late responses.
            page.evaluate("() => { window.fixture.setMounted(false); window.fixture.setPath('race/A.md'); window.fixture.setPair(true) }")
            page.wait_for_function('window.fixture.requests().length===1')
            page.evaluate('window.fixture.setSecond(false)')
            assert page.evaluate('window.fixture.requests()[0].aborted') is False
            page.evaluate("window.fixture.setPath('race/B.md')")
            page.wait_for_function('window.fixture.requests().length===2')
            page.evaluate("window.fixture.setPath('race/A.md')")
            page.wait_for_function('window.fixture.requests().length===3')
            page.evaluate("() => { window.fixture.resolve(0,'# Stale A'); window.fixture.resolve(1,'# Wrong B') }")
            assert page.locator('[data-file-path="race/A.md"]').inner_text()==''
            page.evaluate("window.fixture.resolve(2,'# Current A\\n\\n$x^2$')")
            page.wait_for_selector('[data-file-path="race/A.md"] h1')
            assert 'Current A' in page.locator('[data-file-path="race/A.md"]').inner_text()
            page.evaluate("window.fixture.setPath('race/unmount.md')")
            page.wait_for_function('window.fixture.requests().length===4')
            page.evaluate('window.fixture.setPair(false)')
            page.wait_for_function('window.fixture.requests()[3].aborted')
            page.evaluate("window.fixture.resolve(3,'# Unmounted')")
            assert page.locator('[data-file-path]').count()==0
            # F4 executes build-time extracted actual App handlers/effect in the
            # browser's Solid runtime with a synthetic unchanged card, no study API.
            for matching in [False,True]:
                page.evaluate('window.sourceFixture=window.fixture.startSourceFixture()')
                page.wait_for_function('window.sourceFixture.requests().length===1')
                page.evaluate('(matching)=>window.sourceFixture.relocate(matching?"a":"unrelated","dest")',matching)
                page.wait_for_function('window.sourceFixture.requests().length===2')
                page.evaluate('window.sourceFixture.resolve(0,{path:"a/note.md"})')
                page.wait_for_timeout(0)
                assert page.evaluate('window.sourceFixture.snapshot().source') is None
                fresh='dest/note.md' if matching else 'a/note.md'
                page.evaluate('(path)=>window.sourceFixture.resolve(1,{path})',fresh)
                page.wait_for_function('(path)=>window.sourceFixture.snapshot().source?.path===path',arg=fresh)
                snapshot=page.evaluate('window.sourceFixture.snapshot()')
                assert snapshot['card']==dict(noteId=42,identity='same-card')
                assert snapshot['chunks']==[dict(path=fresh,identity='same-chunk')]
                page.evaluate('window.sourceFixture.dispose()')
                case['diagnostics']['F4-'+('matching' if matching else 'unrelated')]='PASS';save()
            assert not case['errors'], case['errors']
            case['status'] = 'PASS'
        except Exception:
            case['status'] = 'FAIL'
            case['error'] = traceback.format_exc()
            case['diagnostics']['last_menu_trace'] = page.evaluate('window.menuTrace||[]')
            page.screenshot(path=str(out / f'{theme}-{width}-failure.png'), full_page=True)
        finally:
            save()
            page.close()
        if a.fail_fast and case['status']=='FAIL':
            raise SystemExit('Fail-fast: diagnostics saved; remaining planned cases not executed')
    browser.close()
assert all(r['status']=='PASS' for r in records), [(r['theme'],r['width'],r.get('error')) for r in records if r['status']!='PASS']
