"""
Servidor FastAPI para o Painel Web Local do RoboTrader Pro (MT5 + Safirion Bridge).
Oferece endpoints REST e WebSocket (/ws/safirion-bridge) para comunicação em 2º plano
com a guia aberta da Safirion (Binárias, Digital, Blitz e OTC), funcionando mesmo com a janela minimizada.
"""

import asyncio
import re
import uuid
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError
from typing import Literal
from contextlib import suppress

from backend.bot_manager import BotManager
from backend.backtest import BacktestRequest, run_backtest

load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False)
bot = BotManager()
research_lock = asyncio.Lock()


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


@app.post("/api/bot/toggle")
async def toggle_bot():
    if research_lock.locked() or (bot._mt5_busy and not bot.is_running):
        raise HTTPException(409, detail='Aguarde o laboratório encerrar antes de iniciar o robô.')
    if not bot.is_running:
        blocked = bot._risk_block(manual=True)
        if blocked:
            raise HTTPException(409, detail=blocked)
    bot.is_running = not bot.is_running
    if bot.is_running:
        bot.stats["status_reason"] = f"Robô ATIVO monitorando {bot.config['symbol']} ({bot.config['timeframe']})..."
        bot.add_log("START", f"Robô iniciado automaticamente em {bot.config['symbol']} ({bot.config['market_type']}).")
    else:
        bot.stats["status_reason"] = "Robô PAUSADO pelo usuário."
        bot.add_log("STOP", "Robô pausado pelo usuário.")
    bot.notifier.enqueue('toggle:'+uuid.uuid4().hex,bot.telegram_status())
    return {"is_running": bot.is_running, "status_reason": bot.stats["status_reason"]}


@app.post("/api/config")
async def update_config(payload: Dict[str, Any]):
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


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
