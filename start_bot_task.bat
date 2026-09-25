@echo off
REM For use with Windows Task Scheduler — set "Start in" to this project folder,
REM or this cd /d "%~dp0" will resolve it automatically from the script's own location.
cd /d "%~dp0"
python bot.py --dry-run >> logs\task_scheduler.log 2>&1
