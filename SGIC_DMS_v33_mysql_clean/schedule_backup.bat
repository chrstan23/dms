@echo off
rem Creates a Windows scheduled task that backs up SGIC DMS every day at 18:00. Run as Administrator.
setlocal
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (echo Run install_server.bat first. & pause & exit /b 1)
schtasks /create /tn "SGIC DMS Daily Backup" /tr "\"%PY%\" \"%~dp0backup_dms.py\"" /sc daily /st 18:00 /f
if errorlevel 1 (echo Could not create the task. Right-click this file and choose Run as administrator. & pause & exit /b 1)
echo.
echo Done. A backup is made every day at 18:00 into the backups folder (the newest 30 are kept).
echo Copy the backups folder to another computer or drive regularly.
pause
