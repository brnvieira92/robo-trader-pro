"""Run only against an isolated PAPER server; edits its clock configuration."""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8002'
out=Path(os.environ.get('TRADER_SCREENSHOT_DIR','/tmp/mt5-clock-screenshots'));out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        response=page.request.post(base+'/api/config',data={'market_type':'MT5_FOREX','execution_mode':'PAPER',
            'mt5_tick_time_offset_minutes':0,'mt5_bar_time_offset_minutes':0})
        assert response.ok,response.text()
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('Atualizado')")
        page.locator('details.mt5-settings').filter(has=page.locator('#cfgMt5TickOffset')).locator('summary').click()
        assert page.locator('#cfgMt5TickOffset').input_value()=='0'
        assert page.locator('#cfgMt5BarOffset').input_value()=='0'
        page.locator('#cfgMt5TickOffset').fill('180')
        with page.expect_response('**/api/config') as saved:
            page.locator('#cfgMt5TickOffset').press('Tab')
        assert saved.value.ok,saved.value.text()
        page.reload(wait_until='networkidle')
        page.wait_for_function("document.querySelector('#cfgMt5TickOffset').value === '180'")
        page.locator('details.mt5-settings').filter(has=page.locator('#cfgMt5TickOffset')).locator('summary').click()
        assert page.locator('#cfgMt5BarOffset').input_value()=='0'
        assert 'Computador UTC:' in page.locator('#mt5ClockSummary').inner_text()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(out/f'clock-settings-{width}.png'),full_page=True)
        assert not errors,errors
        page.close()
    browser.close()
print('Desktop/mobile: independent clock controls, save/reload, raw/UTC diagnostic, no overflow/errors passed.')
