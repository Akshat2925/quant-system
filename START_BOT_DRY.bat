@echo off
title ETF Bot - DRY RUN (simulated orders)
color 0B
echo ==========================================
echo     ETF BOT - DRY RUN
echo     Simulated orders, no real money
echo ==========================================
echo.
cd /d "%~dp0"
python bot.py --dry-run
pause
