@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo SGIC DMS - First-time Windows Server setup
echo ============================================================
where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3 is not installed or is not in PATH.
    echo Install Python 3 from https://www.python.org/downloads/windows/
    echo During setup, enable Add Python to PATH.
    pause
    exit /b 1
  )
  set PYTHON=python
) else (
  set PYTHON=py -3
)
if not exist venv\Scripts\python.exe (
  %PYTHON% -m venv venv
  if errorlevel 1 goto error
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto error
python -m pip install -r requirements.txt
if errorlevel 1 goto error
if not exist backups mkdir backups
if not exist uploads mkdir uploads
echo.
echo Setup completed. Next: run check_install.bat, then start_server.bat.
echo Demo accounts are listed in README_SETUP.txt; each must set a new password at first login.
pause
exit /b 0
:error
echo.
echo Setup failed. Check the error above and your internet connection.
pause
exit /b 1
