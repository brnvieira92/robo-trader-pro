@echo off
title ProspeTraderBot - MT5 & Safirion Unified
echo ========================================================
echo   Iniciando ProspeTraderBot (Safirion OB + MetaTrader 5)
echo ========================================================
cd /d "%~dp0"
if not exist ".venv" (
    echo Criando ambiente virtual Python...
    python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar dependencias. Confira Python 64 bits e a mensagem acima.
    pause
    exit /b 1
)
echo.
echo Painel disponivel em: http://localhost:8000
start http://localhost:8000
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
