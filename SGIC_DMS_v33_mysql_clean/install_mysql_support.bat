@echo off
setlocal
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo ERROR: Run install_server.bat first to create the Python virtual environment.
  pause
  exit /b 1
)
call venv\Scripts\activate.bat
python -m pip install -r requirements-mysql.txt
if errorlevel 1 goto error
echo.
echo MySQL Python driver installed.
echo Next: check database.ini, then run check_mysql.bat and start_server.bat.
pause
exit /b 0
:error
echo MySQL driver installation failed. Check the error above and internet connection.
pause
exit /b 1
