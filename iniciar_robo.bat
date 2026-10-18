@echo off
title RoboTrader Pro - MT5 & Safirion
echo ========================================================
echo   Iniciando RoboTrader Pro (Safirion OB + MetaTrader 5)
echo ========================================================
cd /d "%~dp0"
if not exist ".venv" (
    echo Criando ambiente virtual Python...
    python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt --quiet
echo.
echo Painel disponivel em: http://localhost:8000
start http://localhost:8000
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause
