"""Profit exit controls and honest pending labels, fake SDK, no trading POST."""
import os,sys
from playwright.sync_api import sync_playwright
base=sys.argv[1]


def save_policy(page,enabled):
    for _ in range(8):
        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/config')) as response:
            page.evaluate('''(enabled)=>{document.querySelector('#cfgProfitExitEnabled').checked=enabled;
                document.querySelector('#cfgProfitExitMin').value=enabled?'2':'1';saveConfig();}''',enabled)
        if response.value.status==200:
            page.wait_for_function('(enabled)=>latestState.config.profit_exit.enabled===enabled',arg=enabled)
            return
        assert response.value.status==409,response.value.text()
        page.wait_for_timeout(250)
    raise AssertionError('Fake server remained busy')


with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    for width,height in [(1440,1100),(390,844)]:
        page=browser.new_page(viewport=dict(width=width,height=height));errors=[];orders=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:orders.append(r.url) if r.method=='POST' and '/api/order/' in r.url else None)
        page.goto(base,wait_until='networkidle');page.wait_for_function('latestState?.config?.profit_exit')
        original=page.evaluate('({s:latestState.config.strategy_id,r:latestState.config.risk_per_trade_pct,n:latestState.config.forex_max_open_positions})')
        save_policy(page,True)
        assert page.locator('#cfgProfitExitMin').input_value()=='2'
        assert not page.evaluate('latestState.is_running')
        assert original==page.evaluate('({s:latestState.config.strategy_id,r:latestState.config.risk_per_trade_pct,n:latestState.config.forex_max_open_positions})')
        save_policy(page,False)
        mode={'value':'PENDING'}
        def modify(route):
            response=route.fetch();data=response.json()
            data['trades']=[dict(id='visual-only',status='OPEN',mode='LIVE_MT5',symbol='EURUSD#',action='BUY',currency='USD',
                amount=5,time='10:00',source='Teste visual',pnl=0,filled_volume=.03,close_intent=dict(status=mode['value']))]
            route.fulfill(response=response,json=data)
        page.route('**/api/state',modify)
        page.reload(wait_until='networkidle')
        page.wait_for_function("document.querySelector('#tradesContainer').textContent.includes('FECHAMENTO EM CONFIRMAÇÃO')")
        assert 'WIN' not in page.locator('#tradesContainer').text_content()
        mode['value']='PARTIAL_REVIEW';page.reload(wait_until='networkidle')
        page.wait_for_function("document.querySelector('#tradesContainer').textContent.includes('FECHAMENTO PARCIAL')")
        assert 'WIN' not in page.locator('#tradesContainer').text_content()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert not errors and not orders,(errors,orders)
        page.close()
    browser.close()
print('Desktop/mobile profit-exit controls, pending/partial labels, no orders and preserved risk passed.')
