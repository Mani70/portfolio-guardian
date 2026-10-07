@echo off
rem Swing decisions after the close. Task Scheduler: weekdays 15:45.
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
".venv\Scripts\python.exe" -m trader.run swing-plan >> "logs\scheduled.log" 2>&1
