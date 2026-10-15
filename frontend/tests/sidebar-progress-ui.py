"""Isolated NavRail fixture: intercept every request; never contact an API."""
import argparse, json, mimetypes, subprocess
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=True)
diagnostics = {'cases': []}
def save_diagnostics():
    (a.output / 'diagnostics.json').write_text(json.dumps(diagnostics, indent=2))
def stage(case, name):
    case['stage'] = name
    case['stages'].append(name)
    save_diagnostics()
save_diagnostics()
dist = a.output / 'fixture-dist'
script = """import {build} from 'vite';import solid from 'vite-plugin-solid';
await build({configFile:false,root:'tests',plugins:[solid()],build:{outDir:process.argv[1],emptyOutDir:true,rollupOptions:{input:'tests/sidebar-progress.html'}}});"""
subprocess.run(['node','--input-type=module','-e',script,str(dist)],cwd=ROOT/'frontend',check=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
    for width,height in [(1440,900),(390,844),(390,480)]:
        case = {'viewport': {'width': width, 'height': height},
                'status': 'running', 'stages': []}
        diagnostics['cases'].append(case)
        stage(case, 'create-page')
        page = None
        try:
            page = browser.new_page(viewport={'width':width,'height':height})
            def route(r):
                path = urlparse(r.request.url).path
                assert not path.startswith('/api/'), path
                f = dist / path.lstrip('/'); assert f.is_file(), path
                r.fulfill(body=f.read_bytes(),content_type=mimetypes.guess_type(f)[0] or 'application/octet-stream')
            page.route('**/*', route)
            stage(case, 'load-fixture')
            page.goto('http://sidebar.fixture/sidebar-progress.html')
            nav = page.get_by_role('navigation',name='主导航')
            rings = nav.get_by_role('progressbar')
            stage(case, 'ring-counts-accessibility')
            expect(rings).to_have_count(2)
            assert page.get_by_role('progressbar').count() == 2
            for i,(label,done,total) in enumerate([('阅读',2,7),('复习',3,9)]):
                ring = rings.nth(i)
                expect(ring).to_have_attribute('aria-label',label)
                expect(ring).to_have_attribute('aria-valuenow',str(done))
                expect(ring).to_have_attribute('aria-valuemax',str(total))
                expect(ring).to_have_attribute('aria-valuetext',f'{label} {done}/{total}')
                expect(ring).to_contain_text(f'{done}/{total}')
            stage(case, 'ring-geometry')
            nb = nav.bounding_box(); rb = [rings.nth(i).bounding_box() for i in range(2)]
            case['geometry'] = {'navigation': nb, 'rings': rb}
            case['screenshot'] = str(a.output/f'{width}x{height}.png')
            save_diagnostics()
            page.screenshot(path=case['screenshot'])
            for b in rb:
                assert nb['x'] <= b['x'] and b['x']+b['width'] <= nb['x']+nb['width'], (nb,rb)
            assert rb[0]['y']+rb[0]['height'] < rb[1]['y'], rb
            assert rb[1]['y']+rb[1]['height'] <= height and rb[1]['y'] > height-130
            assert page.locator('.nav-rail__footer').evaluate('(e)=>getComputedStyle(e).position') != 'fixed'
            stage(case, 'dynamic-reading-label')
            reading = nav.get_by_role('button',name='阅读 (7)',exact=True)
            expect(reading).to_be_visible(); assert '清单' not in nav.inner_text()
            page.get_by_role('button',name='Change fixture count').click()
            expect(nav.get_by_role('button',name='阅读 (12)',exact=True)).to_be_visible()
            for label,section in [('文件','files'),('阅读 (12)','readingList'),('学习','study')]:
                button = nav.get_by_role('button',name=label,exact=True)
                # A real Tab establishes keyboard modality after the fixture mouse click.
                # Walk the focus order rather than relying on programmatic focus.
                stage(case, f'keyboard-focus:{section}')
                for _ in range(6):
                    page.keyboard.press('Tab')
                    if button.evaluate('(e)=>e === document.activeElement'):
                        break
                expect(button).to_be_focused()
                assert button.evaluate('(e)=>e.matches(":focus-visible")')
                stage(case, f'button-geometry:{section}')
                b=button.bounding_box(); case.setdefault('buttons', {})[section] = b
                save_diagnostics()
                assert b['y']+b['height'] < rb[0]['y']
                stage(case, f'keyboard-activate:{section}')
                page.keyboard.press('Enter'); expect(page.get_by_test_id('section')).to_have_text(section)
                expect(button).to_have_attribute('aria-current','page')
            stage(case, 'reading-color')
            assert rings.nth(0).locator('.progress-ring__arc').evaluate('(e)=>getComputedStyle(e).stroke') == 'rgb(0, 106, 96)'
            case['status'] = 'passed'
            stage(case, 'complete')
        except Exception as error:
            case['status'] = 'failed'
            case['error'] = f'{type(error).__name__}: {error}'
            save_diagnostics()
            raise
        finally:
            if page is not None:
                page.close()
    browser.close()
print('PASS: sidebar ring placement/order, counts/accessibility/color, dynamic label and usable responsive navigation')
