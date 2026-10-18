"""
Servidor FastAPI para o Painel Web Local do RoboTrader Pro (MT5 + Safirion Bridge).
Oferece endpoints REST e WebSocket (/ws/safirion-bridge) para comunicação em 2º plano
com a guia aberta da Safirion (Binárias, Digital, Blitz e OTC), funcionando mesmo com a janela minimizada.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.bot_manager import BotManager

bot = BotManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop_task = asyncio.create_task(bot.run_loop())
    yield
    loop_task.cancel()
    await bot.safirion.close()


app = FastAPI(title="ProspeTraderBot - MT5 & Safirion Unified WebApp", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    action: str  # "BUY" ou "SELL"


@app.websocket("/ws/safirion-bridge")
async def safirion_bridge_ws(websocket: WebSocket):
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
    return JSONResponse(bot.get_market_snapshot())


@app.post("/api/bot/toggle")
async def toggle_bot():
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
    updated = bot.update_config(payload)
    if "safirion_sub_type" in payload:
        bot.safirion.sub_type = payload["safirion_sub_type"]
    return {"status": "ok", "config": updated}


@app.post("/api/connect/mt5")
async def connect_mt5(req: MT5ConnectRequest):
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
    signal = snapshot["signal"] or {
        "symbol": bot.config["symbol"],
        "price": snapshot["candles"][-1]["close"] if snapshot["candles"] else 0.0,
        "confidence": 100.0,
        "stop_loss": None,
        "take_profit": None,
    }
    signal["action"] = req.action.upper()
    res = await bot.execute_trade_from_signal(signal, manual=True)
    return res


@app.post("/api/stats/reset")
async def reset_stats():
    bot.stats["daily_pnl"] = 0.0
    bot.stats["wins"] = 0
    bot.stats["losses"] = 0
    bot.stats["total_trades"] = 0
    bot.stats["win_rate"] = 0.0
    bot.stats["current_gale_step"] = 0
    bot.trade_history.clear()
    bot.add_log("INFO", "Placar e histórico diário zerados pelo usuário.")
    return {"status": "ok", "stats": bot.stats}


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
