@echo off
rem Run on the HOST PC (while the server is running). Send a screenshot of this window.
echo ===== 1. This PC's IPv4 addresses =====
ipconfig | findstr /i "IPv4 adapter"
echo.
echo ===== 2. Is the server listening on port 5000? (want 0.0.0.0:5000 LISTENING) =====
netstat -an | findstr ":5000" | findstr /i "LISTENING"
echo.
echo ===== 3. Network profile (want Private, Public may block) =====
powershell -NoProfile -Command "Get-NetConnectionProfile | Format-Table Name,InterfaceAlias,NetworkCategory -AutoSize"
echo ===== 4. Firewall rules for port 5000 =====
powershell -NoProfile -Command "Get-NetFirewallRule -DisplayName 'SGIC DMS*' | Format-Table DisplayName,Enabled,Direction,Action,Profile -AutoSize"
echo.
echo ===== 5. Does the page answer on this PC? =====
powershell -NoProfile -Command "try{(Invoke-WebRequest http://127.0.0.1:5000/healthz -UseBasicParsing -TimeoutSec 5).StatusCode}catch{'FAILED: '+$_.Exception.Message}"
echo.
echo ===== 6. Does it answer on this PC's LAN IP? =====
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do powershell -NoProfile -Command "try{'%%b -> '+(Invoke-WebRequest http://%%b:5000/healthz -UseBasicParsing -TimeoutSec 5).StatusCode}catch{'%%b -> FAILED'}"
)
echo.
pause
