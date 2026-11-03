@echo off
setlocal
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Please run install_server.bat first.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python serve.py
pause
