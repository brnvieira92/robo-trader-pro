"""Use only multi_browser_server.py: fake SDK, temporary ledger, paused bot."""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8021'
out=Path(os.environ.get('TRADER_SCREENSHOT_DIR','/tmp/forex-research-screenshots'));out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height});errors=[];trading=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:trading.append(r.url) if r.method=='POST' and ('/api/order/' in r.url or '/api/bot/' in r.url) else None)
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("latestState?.forex_radar?.rows?.some(r=>r.checks?.length)")
        card=page.locator('#forexRadarRows article').first
        card.locator('summary').first.click()
        assert 'Exigido:' in card.inner_text() and 'Brier' in card.inner_text()
        page.wait_for_timeout(1800)
        assert card.locator('details').first.get_attribute('open') is not None
        page.locator('#pairComparison summary').click()
        page.get_by_role('button',name='Comparar pares',exact=True).click()
        page.wait_for_function("document.querySelector('#pairReportTable td')!==null")
        assert 'EURUSD#' in page.locator('#pairReportTable').inner_text()
        with page.expect_download() as report_download:page.locator('#pairReportDownload').click()
        report_download.value.save_as(str(out/f'pares-{width}.json'))
        page.locator('#forexResearch summary').click()
        with page.expect_response('**/api/research/forex/history?count=1000') as capture:
            page.locator('#forexHistoryLoad').click()
        assert capture.value.ok,capture.value.text()
        page.wait_for_function("forexDataset?.markets?.length===4")
        assert page.evaluate('forexDataset.config.forex_max_open_positions')==2
        page.locator('#forexResearchStrategy').select_option('forex_triple_atr')
        page.locator('#forexExitMode').select_option('BREAK_EVEN_ATR')
        page.locator('#forexResearchSlots').select_option('2')
        page.locator('#forexEvaluatedBars').fill('2')
        page.locator('#forexAssumptions').check()
        with page.expect_response('**/api/research/forex/jobs') as started:
            page.locator('#forexResearchRun').click()
        assert started.value.status==202,started.value.text()
        submitted=started.value.request.post_data_json
        assert submitted['config']['strategy_id']=='forex_triple_atr'
        assert submitted['exit_management']['mode']=='BREAK_EVEN_ATR'
        page.wait_for_function("document.querySelector('#forexResearchOutput').textContent.includes('Backtest Forex •')",timeout=60000)
        assert 'SEM OPERAÇÕES' in page.locator('#forexResearchOutput').inner_text()
        assert 'Proteção + trailing ATR' in page.locator('#forexResearchOutput').inner_text()
        assert 'Triplo ATR' in page.locator('#forexResearchOutput').inner_text()
        assert 'Limite global: 2' in page.locator('#forexResearchOutput').inner_text()
        assert 'Diagnóstico financeiro por grupo' in page.locator('#forexResearchOutput').inner_text()
        assert page.evaluate('latestState.config.forex_max_open_positions')==2
        assert page.evaluate('latestState.config.strategy_id')=='ml_random_forest'
        assert page.evaluate('forexDataset.config.strategy_id')=='ml_random_forest'
        assert page.locator('#forexResearchExports a').count()==3
        with page.expect_download() as download:page.get_by_text('Baixar resultado completo JSON',exact=True).click()
        download.value.save_as(str(out/f'backtest-{width}.json'))
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(out/f'forex-research-{width}.png'),full_page=True)
        assert not trading,trading
        assert not errors,errors
        page.close()
    browser.close()
print('Desktop/mobile: complete filters, pair report, JSON download, fake MT5 capture, Forex job/result/exports, no trading requests or overflow/errors passed.')
