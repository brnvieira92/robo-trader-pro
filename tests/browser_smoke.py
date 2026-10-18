"""Run against an isolated local server: python tests/browser_smoke.py [base_url]."""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000'
out=Path(os.environ.get('TRADER_SCREENSHOT_DIR','/tmp/trader-screenshots'));out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height})
        errors=[];external=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:external.append(request.url) if not request.url.startswith(base) else None)
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('Atualizado')")
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert page.locator('#chartContainer canvas').count()>0
        assert not errors,errors
        assert not external,external
        page.locator('button[onclick="openConnectionsModal()"]').click()
        assert page.get_by_role('dialog').is_visible()
        page.keyboard.press('Escape')
        assert not page.get_by_role('dialog').is_visible()
        page.screenshot(path=str(out/f'dashboard-{width}.png'),full_page=True)
        # A rejected manual request must show its actual reason, never a success toast.
        page.route('**/api/order/manual',lambda route:route.fulfill(status=409,content_type='application/json',body='{"detail":"Limite de risco: teste"}'))
        page.locator('#btn-call').click()
        page.get_by_text('Limite de risco: teste',exact=True).wait_for()
        page.route('**/api/state',lambda route:route.abort())
        page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('OFFLINE')")
        assert page.locator('#btn-call').is_disabled()
        assert page.locator('#btnToggleBot').is_disabled()
        assert not errors,errors
        page.close()
    browser.close()
print('Desktop/mobile, chart, local assets, modal, API failure and offline states passed.')
