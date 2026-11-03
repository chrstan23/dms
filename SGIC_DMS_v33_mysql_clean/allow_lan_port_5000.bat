@echo off
rem Right-click -> Run as administrator. Opens the firewall for SGIC DMS (LAN only).
net session >nul 2>nul
if errorlevel 1 (echo Please right-click this file and choose Run as administrator. & pause & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0allow_lan_port_5000.ps1"
pause
