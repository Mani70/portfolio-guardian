@echo off
rem Runs the portfolio guardian once. Used by Windows Task Scheduler (weekdays 15:45 IST).
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
".venv\Scripts\python.exe" -m guardian.main >> "logs\scheduled.log" 2>&1
