@echo off
rem Intraday scanner. Task Scheduler: weekdays 09:10 (it runs until ~15:20).
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
".venv\Scripts\python.exe" -m trader.run intraday >> "logs\scheduled.log" 2>&1
