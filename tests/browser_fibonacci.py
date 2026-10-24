"""Read-only Fibonacci UI checks against an isolated fake-SDK server."""
import os
import sys
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8011'
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),
                              headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height})
        errors=[];writes=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("latestState?.signal?.indicators?.fibonacci?.available")
        assert 'Retração 61.8%' in page.locator('#fibonacciContext').text_content()
        assert 'não aprova sinais' in page.locator('#fibonacciContext').text_content()
        rows=page.evaluate('latestState.forex_radar.rows')
        assert len(rows)==4 and all(r['fibonacci']['available'] for r in rows)
        assert page.get_by_text('Fibonacci — informativo',exact=True).count()==5
        page.locator('details[data-pair="fibonacci:EURUSD"] summary').click()
        page.evaluate('renderForexRadar(latestState)')
        assert page.locator('details[data-pair="fibonacci:EURUSD"]').get_attribute('open') is not None
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        # Broker-derived strings must remain text and cannot become HTML.
        page.evaluate("""() => {
          const state=structuredClone(latestState);
          state.signal.indicators.fibonacci={available:false,reason:'<img src=x onerror=alert(1)>'};
          renderAudit(state);
        }""")
        assert page.locator('#fibonacciContext img').count()==0
        assert '<img' in page.locator('#fibonacciContext').text_content()
        assert not errors and not writes,(errors,writes)
        page.close()
    browser.close()
print('Fibonacci browser checks passed (desktop/mobile).')
