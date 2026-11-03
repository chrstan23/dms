@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Run install_server.bat first.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python change_password.py
pause
