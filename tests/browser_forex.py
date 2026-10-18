"""Use an isolated PAPER server: python tests/browser_forex.py http://127.0.0.1:8001."""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8001'
out=Path(os.environ.get('TRADER_SCREENSHOT_DIR','/tmp/forex-screenshots'));out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    response=browser.new_context().request.post(base+'/api/config',data={
        'market_type':'MT5_FOREX','execution_mode':'PAPER','stop_gain':150,'goal_currency':'BRL','cooldown_seconds':5,
        'strategy_params':{'use_structure_stops':False,'use_structure_targets':False,'max_entry_drift_atr':5}})
    assert response.ok,response.text()
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('Atualizado')")
        assert not page.locator('#cfgExpiration').is_visible()
        assert not page.locator('#cfgPayout').is_visible()
        assert page.locator('#pricePlanSettings').is_visible()
        assert page.locator('#manualBuyLabel').inner_text()=='BUY'
        assert page.locator('#manualSellLabel').inner_text()=='SELL'
        assert 'PAPER selecionado' in page.locator('#executionDiagnosis').inner_text()
        assert '150' in page.locator('#lblMetaGain').inner_text()
        assert 'BUY' in page.locator('#pricePlanPreview').inner_text()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.wait_for_function("!latestState.risk.blocked_reason?.includes('Intervalo mínimo')",timeout=65000)
        with page.expect_response('**/api/order/manual') as order:
            page.locator('#btn-call').click()
        assert order.value.ok,order.value.text()
        close=page.locator('[data-close-paper]')
        close.wait_for()
        assert 'SL/TP' in page.locator('#tradesContainer').inner_text()
        assert 'Entrada' in page.locator('#tradesContainer').inner_text()
        assert not page.locator('#tradesContainer .material-symbols-outlined').count()
        page.screenshot(path=str(out/f'forex-open-{width}.png'),full_page=True)
        close.click()
        page.wait_for_function("!document.querySelector('[data-close-paper]')")
        assert 'MANUAL' in page.locator('#tradesContainer').inner_text()
        assert not errors,errors
        page.close()
    browser.close()
print('Forex desktop/mobile: PAPER diagnosis, no expiration/payout, price plans, open SL/TP, manual close passed.')
