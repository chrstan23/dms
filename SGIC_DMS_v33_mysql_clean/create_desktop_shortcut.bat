@echo off
setlocal
set SERVERIP=192.168.0.202
set /p SERVERIP=Server address [default %SERVERIP%]:
if "%SERVERIP%"=="" (
  echo No server address entered.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='http://%SERVERIP%:5000'; $p=[Environment]::GetFolderPath('Desktop'); $f=Join-Path $p 'SGIC DMS.url'; $content='[InternetShortcut]'+[Environment]::NewLine+'URL='+$u; Set-Content -LiteralPath $f -Value $content -Encoding ASCII; Write-Host ('Created desktop shortcut: '+$f); Write-Host ('Target: '+$u)"
if errorlevel 1 echo Could not create shortcut. Try running this file from the Windows desktop session.
echo.
echo Double-click SGIC DMS on the Desktop to open the central server.
pause
