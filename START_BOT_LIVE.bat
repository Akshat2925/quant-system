@echo off
title ETF Trading Bot - LIVE
color 0C
echo ==========================================
echo     ETF TRADING BOT - LIVE
echo     *** REAL MONEY MODE ***
echo ==========================================
echo.
echo WARNING: This will place REAL orders!
echo Press any key to continue or close this window to cancel...
pause
echo.
cd /d "%~dp0"
python bot.py
pause
