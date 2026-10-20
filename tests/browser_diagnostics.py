"""Read-only diagnostics UI against an isolated local server."""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8004'
out=Path(os.environ.get('TRADER_SCREENSHOT_DIR','/tmp/diagnostics-screenshots'));out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height});errors=[];writes=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('Atualizado')")
        assert 'Pausado pelo usuário' in page.locator('#operationalStatus').inner_text()
        page.locator('#decisionDiagnostics summary').click()
        page.get_by_role('button',name='Atualizar registros',exact=True).click()
        page.wait_for_function("document.querySelector('#decisionDiagnosticOutput').textContent.includes('ANALYSIS')")
        assert 'Bloqueios:' in page.locator('#decisionDiagnosticOutput').inner_text()
        with page.expect_download() as download:
            page.locator('#diagnosticExport').click()
        download.value.save_as(str(out/f'diagnosticos-{width}.csv'))
        assert not writes,writes
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(out/f'diagnostics-{width}.png'),full_page=True)
        assert not errors,errors
        page.close()
    browser.close()
print('Desktop/mobile: runtime status, persistent events, CSV download, no trading POSTs, no overflow/errors passed.')
