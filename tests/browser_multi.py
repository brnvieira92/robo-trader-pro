"""Run only against a local fake-SDK server, paused, using an isolated journal.

The Radar itself must make no POST requests. A separate explicit settings test
then verifies selection persistence, without starting the bot or submitting trades.
"""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8011'
out=Path(os.environ.get('TRADER_SCREENSHOT_DIR','/tmp/multi-screenshots'));out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height});errors=[];writes=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('Atualizado')")
        assert page.locator('#forexRadarRows article').count()==4
        assert 'EURUSD#' in page.locator('#forexRadarRows').inner_text()
        assert 'Confiança ML:' in page.locator('#forexRadarRows').inner_text()
        assert page.locator('#btn-call').is_disabled() and page.locator('#btn-put').is_disabled()
        page.wait_for_timeout(2100)
        assert not writes,writes
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(out/f'radar-{width}.png'),full_page=True)
        assert not errors,errors
        page.close()
    # Selecting a pair is a separate configuration action, outside the Radar.
    page=browser.new_page(viewport={'width':1440,'height':1100})
    page.goto(base,wait_until='networkidle')
    page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('Atualizado')")
    page.get_by_text('Configurar pares monitorados',exact=True).click()
    page.locator('#cfgPairEnabledUSDCHF').uncheck()
    page.locator('#cfgSaveForexPairs').click()
    page.wait_for_function("latestState?.config?.forex_pairs?.find(p => p.pair==='USDCHF')?.enabled===false")
    page.reload(wait_until='networkidle')
    page.wait_for_function("document.querySelector('#lastUpdated').textContent.includes('Atualizado')")
    assert not page.locator('#cfgPairEnabledUSDCHF').is_checked()
    assert not page.evaluate('latestState.is_running')
    # Restore selection; this never starts automation.
    page.get_by_text('Configurar pares monitorados',exact=True).click()
    page.locator('#cfgPairEnabledUSDCHF').check()
    page.locator('#cfgSaveForexPairs').click()
    page.wait_for_function("latestState?.config?.forex_pairs?.find(p => p.pair==='USDCHF')?.enabled===true")
    browser.close()
print('Radar desktop/mobile, read-only polling, disabled manual orders, selection persistence, no overflow/errors passed.')
