@echo off
title ETF Bot - ALERT ONLY (no orders)
color 0A
echo ==========================================
echo     ETF BOT - ALERT ONLY
echo     Signals sent to Telegram, NO orders
echo ==========================================
echo.
cd /d "%~dp0"
python bot.py --alert-only
pause
