"""
Servidor FastAPI para o Painel Web Local do RoboTrader Pro (MT5 + Safirion Bridge).
Oferece endpoints REST e WebSocket (/ws/safirion-bridge) para comunicação em 2º plano
com a guia aberta da Safirion (Binárias, Digital, Blitz e OTC), funcionando mesmo com a janela minimizada.
"""

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError
from typing import Literal
from contextlib import suppress

from backend.bot_manager import BotManager
from backend.backtest import BacktestRequest, run_backtest

bot = BotManager()
research_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop_task = asyncio.create_task(bot.run_loop())
    try:
        yield
    finally:
        loop_task.cancel()
        with suppress(asyncio.CancelledError):
            await loop_task
        await bot.close()


app = FastAPI(title="ProspeTraderBot - MT5 & Safirion Unified WebApp", lifespan=lifespan)

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
    use_simulation_if_unavailable: bool = True


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
    if research_lock.locked():
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
    if bot.is_running or bot._has_open_trade:
        raise HTTPException(409, detail='Pause e encerre a operação antes de trocar a fonte de dados.')
    res = bot.mt5.connect(
        login=req.login,
        password=req.password,
        server=req.server,
        path=req.path,
        use_simulation_if_unavailable=req.use_simulation_if_unavailable,
    )
    bot.add_log("MT5", res["message"])
    return res


@app.post("/api/connect/safirion")
async def connect_safirion(req: SafirionConnectRequest):
    if bot.is_running or bot._has_open_trade:
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
    if not res['success']:
        raise HTTPException(409, detail=res['message'])
    return res


@app.post("/api/stats/reset")
async def reset_stats():
    # Risk history is an audit trail, not an editable scoreboard.
    raise HTTPException(409, detail="Histórico preservado para os limites de risco. A sessão usa o dia UTC; reiniciar não zera perdas.")


@app.post('/api/research/backtest')
async def research_backtest(payload: BacktestRequest):
    if research_lock.locked() or bot.is_running or bot._has_open_trade:
        raise HTTPException(409, detail='Pause a operação e aguarde o processamento atual antes de executar o laboratório.')
    async with research_lock:
        return await asyncio.to_thread(run_backtest, payload)


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
