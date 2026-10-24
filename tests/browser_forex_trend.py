"""Strategy selection on an isolated fake SDK, never starts trading."""
import os
import sys
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8011'


def select_strategy(page,value):
    # Configuration can correctly return 409 while the serial SDK reader owns
    # the lock. Retry only that explicit rejection, on this fake server.
    for _ in range(6):
        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/config')) as response:
            page.locator('#cfgStrategy').select_option(value)
        if response.value.status==200:
            page.wait_for_function('(value) => latestState?.config?.strategy_id===value',arg=value)
            return
        assert response.value.status==409,response.value.text()
        page.wait_for_timeout(250)
    raise AssertionError('Configuration remained busy on isolated server')


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),
                              headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height});errors=[];orders=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:orders.append(r.url) if r.method=='POST' and '/api/order/' in r.url else None)
        page.goto(base,wait_until='networkidle')
        page.wait_for_function('latestState?.config?.forex_multi_enabled')
        if page.evaluate('latestState.config.strategy_id')!='forex_trend_continuation':
            select_strategy(page,'forex_trend_continuation')
        page.wait_for_function("document.querySelector('#validationLabel').textContent.includes('TÉCNICA')")
        assert 'RF não utilizado' in page.locator('#validationDetail').text_content()
        assert page.locator('#scoreUnit').text_content()=='/100'
        assert page.locator('#barSentBuyers').is_hidden()
        page.wait_for_function("latestState.forex_radar.rows.every(r=>r.uses_ml===false)")
        assert 'Score técnico:' in page.locator('#forexRadarRows').text_content()
        assert 'sem vantagem demonstrada' not in page.locator('#forexRadarRows').text_content()
        assert page.locator('#cfgMinConfidence').input_value()=='78'
        assert not page.evaluate('latestState.is_running')
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors and not orders,(errors,orders)
        page.close()
    page=browser.new_page();page.goto(base,wait_until='networkidle')
    page.wait_for_function('latestState?.config?.forex_multi_enabled')
    select_strategy(page,'ml_random_forest')
    browser.close()
print('Trend strategy desktop/mobile selection, independent technical labels and no orders passed.')
