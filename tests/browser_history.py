"""Persistent history UI, read-only on an isolated fake-SDK server."""
import json
import os
import sys
from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8011'
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),
                                headless=True, args=['--no-sandbox'])
    for width, height in [(1440, 1100), (390, 844)]:
        page = browser.new_page(viewport={'width': width, 'height': height})
        errors, writes = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' else None)
        page.goto(base, wait_until='networkidle')
        page.get_by_text('Histórico persistente — todos os dias', exact=True).click()
        page.get_by_role('button', name='Consultar histórico', exact=True).click()
        page.wait_for_function("document.querySelector('#persistentTrades').textContent.includes('0 de 0')")
        with page.expect_download() as download:
            page.get_by_role('link', name='Exportar histórico JSON', exact=True).click()
        assert json.load(open(download.value.path()))['trades'] == []
        # Long-lived history and broker strings render as text, without HTML execution.
        page.route('**/api/trades/history?limit=200', lambda route: route.fulfill(json=dict(total=1, items=[
            dict(day='2026-10-20', time='03:57', symbol='<img src=x onerror=alert(1)>',
                 status='WIN', pnl=14.96, currency='USD')
        ])))
        page.get_by_role('button', name='Consultar histórico', exact=True).click()
        page.wait_for_function("document.querySelector('#persistentTrades').textContent.includes('14.96 USD')")
        assert page.locator('#persistentTrades img').count() == 0
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors and not writes, (errors, writes)
        page.close()
    browser.close()
print('Persistent history browser checks passed (desktop/mobile).')
