import pytest
from fastapi.testclient import TestClient
from backend.bot_manager import BotManager

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADER_JOURNAL_PATH',str(tmp_path/'api.db'))
    import backend.main as main
    monkeypatch.setattr(main,'bot',BotManager())
    with TestClient(main.app) as c:
        yield c

def test_api_returns_explicit_paper_source_and_healthy_assets(client):
    state=client.get('/api/state').json()
    assert state['execution_mode']=='PAPER' and state['data_quality']['synthetic']
    assert len(state['candles'])==60
    assert client.get('/').status_code==200
    for path in ['dashboard.css','lightweight-charts.standalone.production.js','material-symbols-outlined.woff2']:
        assert client.get('/static/vendor/'+path).status_code==200

def test_api_invalid_config_never_mutates_state(client):
    before=client.get('/api/state').json()['config']
    assert client.post('/api/config',json={'stop_loss':-1}).status_code==422
    assert client.get('/api/state').json()['config']==before

def test_api_reports_manual_success_and_conflict(client):
    assert client.post('/api/order/manual',json={'action':'BOGUS'}).status_code==422
    res=client.post('/api/order/manual',json={'action':'BUY'})
    assert res.status_code==200 and res.json()['success'] and res.json()['mode']=='PAPER'
    assert client.post('/api/order/manual',json={'action':'SELL'}).status_code==409
    assert client.post('/api/config',json={'symbol':'GBPUSD'}).status_code==409

def test_scoreboard_reset_cannot_erase_risk_history(client):
    assert client.post('/api/stats/reset').status_code==409

def test_bad_backtest_is_rejected(client):
    assert client.post('/api/research/backtest',json={'candles':[]}).status_code==422

def test_bridge_heartbeat_and_disconnect(client):
    with client.websocket_connect('/ws/safirion-bridge') as ws:
        ws.send_json({'type':'BRIDGE_HEARTBEAT'})
        assert ws.receive_json()['type']=='SERVER_PONG'
    state=client.get('/api/state').json()
    assert not state['connections']['safirion']['connected']

def test_untrusted_websocket_origin_cannot_inject_market_data(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/safirion-bridge',headers={'origin':'https://untrusted.example'}):
            pass
