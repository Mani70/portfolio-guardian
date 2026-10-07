@echo off
rem Morning check of swing fills. Task Scheduler: weekdays 09:30.
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
".venv\Scripts\python.exe" -m trader.run swing-check >> "logs\scheduled.log" 2>&1
