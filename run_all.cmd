@echo off
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ec_agent.cli run
pause
