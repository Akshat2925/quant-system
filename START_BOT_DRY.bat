@echo off
title ETF Trading Bot - DRY RUN
color 0A
echo ==========================================
echo     ETF TRADING BOT - DRY RUN
echo ==========================================
echo.
echo Starting bot in DRY RUN mode (no real orders)...
echo.
echo Press Ctrl+C to stop the bot
echo ==========================================
cd /d "%~dp0"
python bot.py --dry-run
pause
