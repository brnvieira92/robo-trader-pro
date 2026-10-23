"""
Servidor FastAPI para o Painel Web Local do RoboTrader Pro (MT5 + Safirion Bridge).
Oferece endpoints REST e WebSocket (/ws/safirion-bridge) para comunicação em 2º plano
com a guia aberta da Safirion (Binárias, Digital, Blitz e OTC), funcionando mesmo com a janela minimizada.
"""

import asyncio
import re
import uuid
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError
from typing import Literal
from contextlib import suppress

from backend.bot_manager import BotManager
from backend.backtest import BacktestRequest, run_backtest
from backend.forex_backtest import ForexBacktestRequest, run_forex_backtest
from backend.forex_history import capture_forex_history
from backend.pair_report import pair_report

load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False)
bot = BotManager()
research_lock = asyncio.Lock()
forex_jobs = {}
forex_tasks = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot.journal.acquire_runtime_lock()
    bot.notifier.start()
    loop_task = asyncio.create_task(bot.run_loop())
    try:
        yield
    finally:
        loop_task.cancel()
        with suppress(asyncio.CancelledError):
            await loop_task
        if forex_tasks:
            await asyncio.gather(*list(forex_tasks), return_exceptions=True)
        await bot.close()


app = FastAPI(title="ProspeTraderBot - MT5 & Safirion Unified WebApp", lifespan=lifespan)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1'])

@app.middleware('http')
async def local_browser_origin(request, call_next):
    origin=request.headers.get('origin')
    if request.method in ('POST','PUT','PATCH','DELETE') and origin:
        parsed=urlsplit(origin)
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('localhost','127.0.0.1') or parsed.netloc != request.headers.get('host'):
            return JSONResponse({'detail':'Origem de navegador não autorizada para alterar ou operar o robô.'},status_code=403)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "https://trade.safirion.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
EXTENSION_DIR = BASE_DIR / "safirion-extension"


class MT5ConnectRequest(BaseModel):
    login: Optional[int] = None
    password: Optional[str] = None
    server: Optional[str] = None
    path: Optional[str] = None
    use_simulation_if_unavailable: bool = False


class SafirionConnectRequest(BaseModel):
    broker_url: str = "https://trade.safirion.com/traderoom"
    email: Optional[str] = None
    password: Optional[str] = None
    account_type: str = "REAL"
    sub_type: str = "BINARY"
    launch_browser: bool = False


class ManualOrderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['BUY', 'SELL']


@app.websocket("/ws/safirion-bridge")
async def safirion_bridge_ws(websocket: WebSocket):
    origin = websocket.headers.get('origin')
    if origin and origin not in {'https://trade.safirion.com', 'http://localhost:8000', 'http://127.0.0.1:8000'} and not re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
        await websocket.close(code=1008)
        return
    await bot.safirion.attach_bridge_websocket(websocket)
    bot.add_log("SAFIRION", "Guia da Safirion conectada em 2º plano via Ponte WebSocket!")
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            log_msg = bot.safirion.handle_bridge_message(
                data, current_timeframe=bot.config.get("timeframe", "M1")
            )
            if msg_type in ("BRIDGE_HEARTBEAT", "BRIDGE_STATUS"):
                try:
                    await websocket.send_json({"type": "SERVER_PONG", "ts": int(asyncio.get_event_loop().time())})
                except Exception:
                    pass
            if log_msg:
                bot.add_log("SAFIRION", log_msg)
    except WebSocketDisconnect:
        bot.safirion.detach_bridge_websocket(websocket)
        bot.add_log("ALERT", "Guia da Safirion foi fechada ou reconectando na Ponte...")
    except Exception as exc:
        bot.safirion.detach_bridge_websocket(websocket)
        bot.add_log("ERROR", f"Erro na Ponte Safirion: {str(exc)}")


@app.get("/api/safirion/bridge.js")
async def get_bridge_script():
    script_path = EXTENSION_DIR / "content_bridge.js"
    return PlainTextResponse(script_path.read_text(encoding="utf-8"), media_type="application/javascript")


@app.get("/api/state")
async def get_state():
    try:
        return JSONResponse(bot.get_market_snapshot())
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(503, detail="Dados de mercado indisponíveis; nenhuma ordem enviada.") from exc


@app.get("/api/forex/radar")
async def forex_radar():
    # Read model only: no analysis, SDK calls or trading side effects.
    if not bot.config.get('forex_multi_enabled'):
        return dict(enabled=False, rows=[], read_only=True)
    return bot.get_market_snapshot()['forex_radar']


def persisted_trade_history():
    """All dates, scoped to the selected mode/account; never changes session risk."""
    mode = bot.config['execution_mode']
    try:
        return [t for t in bot.journal.all_trades()
                if t.get('mode', 'PAPER') == mode and
                (mode != 'LIVE_MT5' or
                 (t.get('account_login'), t.get('account_server')) ==
                 (bot.config['mt5_account_login'], bot.config['mt5_account_server']))]
    except sqlite3.Error as exc:
        raise HTTPException(503, detail='Histórico temporariamente indisponível; registros não foram apagados.') from exc


@app.get('/api/trades/history')
async def trade_history(limit: int=Query(200, ge=1, le=500), offset: int=Query(0, ge=0)):
    trades = persisted_trade_history()
    return dict(items=trades[offset:offset+limit], total=len(trades), offset=offset,
                mode=bot.config['execution_mode'], read_only=True)


@app.get('/api/trades/history.json')
async def trade_history_export():
    return JSONResponse(dict(exported_at=datetime.now(timezone.utc).isoformat(),
                             mode=bot.config['execution_mode'], trades=persisted_trade_history()),
                        headers={'Content-Disposition':'attachment; filename="historico-operacoes.json"'})


@app.get("/api/diagnostics/events")
async def diagnostic_events(symbol: Optional[str]=None,kind: Optional[str]=None,
                            limit: int=Query(100,ge=1,le=500),before: Optional[int]=Query(None,ge=1)):
    try:
        items=bot.journal.diagnostics(symbol,kind,limit,before)
        return dict(items=items,next_before=items[-1]['id'] if len(items)==limit else None,
                    operational=bot.operational_status())
    except sqlite3.Error as exc:
        raise HTTPException(503,detail='Diário temporariamente indisponível; monitoramento e recuperação continuam.') from exc


@app.get("/api/diagnostics/summary")
async def diagnostic_summary():
    try:
        return dict(items=bot.journal.diagnostic_summary(),operational=bot.operational_status())
    except sqlite3.Error as exc:
        raise HTTPException(503,detail='Resumo de diagnósticos temporariamente indisponível.') from exc


@app.get("/api/diagnostics/export.csv")
async def diagnostic_export(symbol: Optional[str]=None,kind: Optional[str]=None,
                            limit: int=Query(1000,ge=1,le=5000),before: Optional[int]=Query(None,ge=1)):
    try:
        rows=bot.journal.diagnostics(symbol,kind,limit,before)
    except sqlite3.Error as exc:
        raise HTTPException(503,detail='Exportação temporariamente indisponível.') from exc
    content=io.StringIO();writer=csv.writer(content)
    writer.writerow(['id','symbol','timeframe','bar_time_source','kind','code','first_seen_utc','last_seen_utc','observations','payload_json'])
    def cell(value):
        value=str(value or '')
        return "'"+value if value.startswith(('=','+','-','@')) else value
    for row in rows:
        writer.writerow([row['id'],cell(row['symbol']),row['timeframe'],cell(row['bar_time']),row['kind'],row['code'],
            datetime.fromtimestamp(row['first_seen'],timezone.utc).isoformat(),datetime.fromtimestamp(row['last_seen'],timezone.utc).isoformat(),
            row['observations'],json.dumps(row['payload'],ensure_ascii=False)])
    headers={'Content-Disposition':'attachment; filename="diagnosticos.csv"'}
    if len(rows)==limit:headers['X-Next-Before']=str(rows[-1]['id'])
    return Response(content='\ufeff'+content.getvalue(),media_type='text/csv; charset=utf-8',headers=headers)


@app.get('/api/diagnostics/pairs')
async def pair_comparison(start: float=Query(0,ge=0), end: Optional[float]=Query(None,ge=0),
                          mode: Literal['PAPER','LIVE_MT5']='LIVE_MT5'):
    import time
    end=time.time() if end is None else end
    if end<start:raise HTTPException(422,detail='Fim deve ser posterior ao início.')
    if mode=='LIVE_MT5' and bot.config.get('mt5_account_login') is None:
        raise HTTPException(409,detail='Vincule a conta MT5 para consultar o relatório LIVE sem misturar contas.')
    try:
        return await asyncio.to_thread(pair_report,bot.journal,start,end,mode,
            bot.config.get('mt5_account_login') if mode=='LIVE_MT5' else None,
            bot.config.get('mt5_account_server') if mode=='LIVE_MT5' else None)
    except sqlite3.Error as exc:
        raise HTTPException(503,detail='Diário temporariamente indisponível.') from exc


@app.post("/api/bot/toggle")
async def toggle_bot():
    if research_lock.locked() or (bot._mt5_busy and not bot.is_running):
        raise HTTPException(409, detail='Aguarde o laboratório encerrar antes de iniciar o robô.')
    if not bot.is_running:
        blocked = bot._risk_block(manual=True)
    else:
        blocked=None
    bot.is_running = not bot.is_running
    if bot.is_running:
        bot.stats["status_reason"] = ("Monitoramento ativo; entradas bloqueadas: "+blocked) if blocked else f"Robô ATIVO monitorando {bot.config['symbol']} ({bot.config['timeframe']})..."
        bot.add_log("START", f"Robô iniciado automaticamente em {bot.config['symbol']} ({bot.config['market_type']}).")
    else:
        bot.stats["status_reason"] = "Robô PAUSADO pelo usuário."
        bot.add_log("STOP", "Robô pausado pelo usuário.")
    bot.notifier.enqueue('toggle:'+uuid.uuid4().hex,bot.telegram_status())
    return {"is_running": bot.is_running, "status_reason": bot.stats["status_reason"]}


@app.post("/api/config")
async def update_config(payload: Dict[str, Any]):
    if research_lock.locked():
        raise HTTPException(409,detail='Aguarde o laboratório concluir antes de alterar a configuração.')
    try:
        updated = bot.update_config(payload)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_context=False, include_input=False)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if "safirion_sub_type" in payload:
        bot.safirion.sub_type = payload["safirion_sub_type"]
    return {"status": "ok", "config": updated}


@app.post("/api/connect/mt5")
async def connect_mt5(req: MT5ConnectRequest):
    paper_positions=[t for t in bot.trade_history if t.get('mode','PAPER')=='PAPER' and t['status']=='OPEN']
    paper_open=any(t.get('exit_policy')!='SL_TP' or t.get('source')!='MT5_LIVE' for t in paper_positions)
    if bot.is_running or paper_open or bot._mt5_busy:
        raise HTTPException(409, detail='Pause e encerre operações PAPER antes de trocar a fonte de dados.')
    # Cache one snapshot before handing the native SDK to a worker thread.
    try:
        bot.get_market_snapshot()
    except (RuntimeError,ValueError):
        pass
    bot._mt5_busy=True
    try:
        res = await asyncio.to_thread(bot.mt5.connect,
        login=req.login,
        password=req.password,
        server=req.server,
        path=req.path,
        use_simulation_if_unavailable=req.use_simulation_if_unavailable,
    )
    finally:
        bot._mt5_busy=False
    bot.add_log("MT5", res["message"])
    if res['status']=='error':
        raise HTTPException(409,detail=res['message'])
    for trade in paper_positions:
        try:
            bot.live.account(trade.get('account_login'),trade.get('account_server'))
        except ValueError as exc:
            raise HTTPException(409,detail=str(exc)) from exc
    if bot.config['execution_mode']=='LIVE_MT5':
        try:
            bot.live.account(bot.config['mt5_account_login'],bot.config['mt5_account_server'])
        except ValueError as exc:
            raise HTTPException(409,detail=str(exc)) from exc
    return res


@app.post("/api/connect/safirion")
async def connect_safirion(req: SafirionConnectRequest):
    if bot.is_running or bot._has_open_trade or bot._mt5_busy:
        raise HTTPException(409, detail='Pause e encerre a operação antes de trocar a conexão.')
    res = await bot.safirion.connect(
        broker_url=req.broker_url,
        email=req.email,
        password=req.password,
        account_type=req.account_type,
        sub_type=req.sub_type,
        launch_browser=req.launch_browser,
    )
    bot.add_log("SAFIRION", res["message"])
    return res


@app.post("/api/order/manual")
async def manual_order(req: ManualOrderRequest):
    if research_lock.locked():
        raise HTTPException(409,detail='Laboratório em execução; aguarde concluir antes de enviar ordens.')
    snapshot = bot.get_market_snapshot()
    signal = {**snapshot["signal"], "action": req.action}
    res = await bot.execute_trade_from_signal(signal, manual=True)
    if res.get('pending'):
        return JSONResponse(res,status_code=202)
    if not res['success']:
        raise HTTPException(409, detail=res['message'])
    return res


@app.get("/api/execution/diagnostics")
async def execution_diagnostics():
    if bot._mt5_busy:
        raise HTTPException(409, detail='MT5 ocupado; aguarde a conclusão da operação.')
    return bot.execution_diagnostics()


@app.post("/api/paper/positions/{identifier}/close")
async def close_paper_position(identifier: str):
    if bot._mt5_busy:
        raise HTTPException(409, detail='MT5 ocupado; aguarde.')
    try:
        return await bot.close_paper_position(identifier)
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@app.post("/api/stats/reset")
async def reset_stats():
    # Risk history is an audit trail, not an editable scoreboard.
    raise HTTPException(409, detail="Histórico preservado para os limites de risco. A sessão usa o dia UTC; reiniciar não zera perdas.")


@app.get('/api/telegram/status')
async def telegram_status():
    return bot.notifier.public_status()


@app.post('/api/telegram/test')
async def telegram_test():
    from backend.telegram_bot import TelegramError
    try:
        return await bot.notifier.test()
    except TelegramError as exc:
        raise HTTPException(409,detail=str(exc)) from exc


@app.post('/api/mt5/reconcile')
async def mt5_reconcile():
    if bot._mt5_busy:
        raise HTTPException(409,detail='MT5 já está processando uma operação. Aguarde.')
    await bot.reconcile_live()
    return {'success':True,'has_open_trade':bot._has_open_trade,
            'trades':[t for t in bot.trade_history if t.get('mode')=='LIVE_MT5']}


@app.post('/api/research/backtest')
async def research_backtest(payload: BacktestRequest):
    if research_lock.locked() or bot.is_running or bot._has_open_trade or bot._mt5_busy:
        raise HTTPException(409, detail='Pause a operação e aguarde o processamento atual antes de executar o laboratório.')
    async with research_lock:
        return await asyncio.to_thread(run_backtest, payload)


async def _wait_forex_research_ready():
    """A paused monitoring cycle may still own the SDK; wait without overlap."""
    loop=asyncio.get_running_loop();deadline=loop.time()+5
    while True:
        if research_lock.locked() or bot.is_running or bot._has_open_trade:
            raise HTTPException(409,detail='Pause e aguarde posições/laboratório antes de iniciar a pesquisa Forex.')
        if not bot._mt5_busy:return
        if loop.time()>=deadline:
            raise HTTPException(409,detail='MT5 ainda processando. Aguarde concluir antes de iniciar a pesquisa Forex.')
        await asyncio.sleep(.05)


@app.post('/api/research/forex/history')
async def forex_history(count: int=Query(1000,ge=150,le=10000)):
    await _wait_forex_research_ready()
    async with research_lock:
        bot._mt5_busy=True
        try:
            return await bot._native_call(capture_forex_history,bot,count)
        except (ValueError,RuntimeError,AttributeError,TypeError) as exc:
            raise HTTPException(409,detail='Falha ao carregar dados reais: '+str(exc)) from exc
        finally:
            bot._mt5_busy=False


async def _run_forex_job(job_id, payload, owner):
    job=forex_jobs[job_id]
    def progress(done,total):job.update(completed_bars=done,total_bars=total)
    try:
        result=await asyncio.to_thread(run_forex_backtest,payload,progress)
        job.update(state='DONE',result=result)
        try:
            await asyncio.to_thread(owner.journal.set_setting,'forex_backtest_last',dict(id=job_id,**job))
        except Exception:
            job['persistence_warning']='Resultado disponível nesta sessão; falha ao salvar no diário. Exporte o JSON.'
    except Exception as exc:
        job.update(state='FAILED',error=str(exc))
    finally:
        owner._research_busy=False
        research_lock.release()


@app.post('/api/research/forex/jobs',status_code=202)
async def start_forex_job(payload: ForexBacktestRequest):
    await _wait_forex_research_ready()
    await research_lock.acquire()
    job_id=uuid.uuid4().hex
    while len(forex_jobs)>=3:forex_jobs.pop(next(iter(forex_jobs)))
    forex_jobs[job_id]=dict(state='RUNNING',completed_bars=0,total_bars=payload.evaluated_bars)
    bot._research_busy=True
    task=asyncio.create_task(_run_forex_job(job_id,payload,bot));forex_tasks.add(task)
    task.add_done_callback(forex_tasks.discard)
    return dict(id=job_id,**forex_jobs[job_id])


def _forex_job(job_id):
    if job_id in forex_jobs:return forex_jobs[job_id]
    saved=bot.journal.setting('forex_backtest_last',{})
    if saved.get('id')==job_id:return saved
    raise HTTPException(404,detail='Avaliação não encontrada nesta sessão ou no último resultado persistido.')


@app.get('/api/research/forex/latest')
async def latest_forex_job():
    return bot.journal.setting('forex_backtest_last',{})


@app.get('/api/research/forex/jobs/{job_id}')
async def forex_job(job_id: str):
    return dict(id=job_id,**_forex_job(job_id))


@app.get('/api/research/forex/jobs/{job_id}/export')
async def export_forex_job(job_id: str, kind: Literal['json','trades','analyses']='json'):
    job=_forex_job(job_id)
    if job['state']!='DONE':raise HTTPException(409,detail='Aguarde o resultado concluir.')
    if kind=='json':
        return Response(json.dumps(job['result'],ensure_ascii=False,allow_nan=False),media_type='application/json',
            headers={'Content-Disposition':'attachment; filename="backtest-forex.json"'})
    rows=job['result'][kind];output=io.StringIO()
    base=['symbol','action','entry_time','exit_time','status','net_pnl'] if kind=='trades' else []
    fields=list(dict.fromkeys([*base,*(k for row in rows for k in row)]))
    writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
    for row in rows:
        clean={}
        for key,value in row.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                clean[key]=value
            else:
                value=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else '' if value is None else str(value)
                clean[key]="'"+value if value.startswith(('=','+','-','@')) else value
        writer.writerow(clean)
    return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="forex-{kind}.csv"'})


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
