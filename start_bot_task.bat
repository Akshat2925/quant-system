@echo off
REM ============================================================
REM  ETF Bot - Windows Task Scheduler launcher
REM
REM  Reads MODE from .env and launches accordingly.
REM  Default (alert_only): signals only, no orders.
REM  Set MODE=live in .env for real orders.
REM
REM  Task Scheduler setup:
REM    Trigger : Daily, weekdays, 09:10 AM
REM    Action  : python bot.py
REM    Start in: D:\etf-trading-bot
REM    Conditions: Wake computer to run this task
REM    Settings  : If already running, do not start again
REM ============================================================

cd /d "%~dp0"

for /f "tokens=1,2 delims==" %%A in ('findstr /i "^MODE=" .env 2^>nul') do set BOT_MODE=%%B
if "%BOT_MODE%"=="" set BOT_MODE=alert_only

echo [%DATE% %TIME%] Starting in MODE=%BOT_MODE% >> logs\task_scheduler.log

if /i "%BOT_MODE%"=="live" (
    python bot.py --live --confirm-live >> logs\task_scheduler.log 2>&1
) else if /i "%BOT_MODE%"=="dry_run" (
    python bot.py --dry-run >> logs\task_scheduler.log 2>&1
) else (
    python bot.py --alert-only >> logs\task_scheduler.log 2>&1
)
