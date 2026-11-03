@echo off
schtasks /end /tn "SGIC DMS Server" >nul 2>nul
schtasks /delete /tn "SGIC DMS Server" /f
pause
