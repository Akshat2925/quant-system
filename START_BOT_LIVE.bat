@echo off
title ETF Bot - LIVE (REAL MONEY)
color 0C

REM Read MODE from .env to verify it is actually set to live
for /f "tokens=1,2 delims==" %%A in ('findstr /i "^MODE=" .env 2^>nul') do set BOT_MODE=%%B

if /i not "%BOT_MODE%"=="live" (
    echo.
    echo ==========================================
    echo   ERROR: MODE in .env is not "live"
    echo   Current MODE=%BOT_MODE%
    echo.
    echo   This launcher is for LIVE trading only.
    echo   Set MODE=live in your .env file first.
    echo   Or use START_BOT_ALERT.bat for safe mode.
    echo ==========================================
    echo.
    pause
    exit /b 1
)

echo ==========================================
echo     ETF BOT - LIVE
echo     *** REAL ORDERS WITH REAL MONEY ***
echo ==========================================
echo.
echo WARNING: This will place REAL buy orders!
echo Press any key to continue or close to cancel...
pause
echo.
cd /d "%~dp0"
python bot.py --live --confirm-live
pause
