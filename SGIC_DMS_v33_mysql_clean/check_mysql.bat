@echo off
setlocal
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo ERROR: Run install_server.bat first.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python check_mysql.py
pause
