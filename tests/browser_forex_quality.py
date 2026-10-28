"""Read-only display and configuration safeguards for RF Forex v3."""
import os
import sys
from playwright.sync_api import sync_playwright

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8011'
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),
                              headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport={'width':width,'height':height});errors=[];writes=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
        page.goto(base,wait_until='networkidle')
        page.wait_for_function("latestState?.config?.strategy_id==='ml_random_forest'")
        assert page.locator('#cfgMt5SpreadStopRatio').input_value()=='30'
        assert 'RF Forex v3' in page.locator('#validationDetail').text_content()
        page.evaluate("""() => {
          const state=structuredClone(latestState);state.is_running=true;
          state.signal.indicators.ml_validation.walk_forward={passed_folds:2};
          renderAudit(state);
        }""")
        assert page.locator('#cfgMt5SpreadStopRatio').is_disabled()
        assert '2/3' in page.locator('#validationDetail').text_content()
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not writes and not errors,(writes,errors)
        page.close()
    browser.close()
print('Forex v3 browser checks passed (desktop/mobile).')
