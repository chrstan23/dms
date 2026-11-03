@echo off
rem Makes SGIC DMS start by itself when this PC starts (no one needs to log in). Run as Administrator.
setlocal
cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\python.exe" (echo Run install_server.bat first. & pause & exit /b 1)
schtasks /create /tn "SGIC DMS Server" /tr "\"%~dp0run_server_task.bat\"" /sc onstart /ru SYSTEM /rl HIGHEST /f
if errorlevel 1 (echo Could not create the task. Right-click this file and choose Run as administrator. & pause & exit /b 1)
schtasks /run /tn "SGIC DMS Server"
echo.
echo Done. SGIC DMS now starts automatically with this PC. Messages are written to logs\dms.log.
echo To remove it later run remove_autostart.bat.
pause
