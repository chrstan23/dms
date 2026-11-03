@echo off
setlocal
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo ERROR: Run install_server.bat first.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python -c "import mysql.connector" >nul 2>nul
if errorlevel 1 (
  echo Installing the MySQL connector for the query browser...
  python -m pip install -r requirements-mysql-browser.txt
  if errorlevel 1 (echo Install failed. Check your internet connection. & pause & exit /b 1)
)
python mysql_query_browser.py
