# Run in PowerShell AS ADMINISTRATOR on the DMS host PC.
# Opens TCP 5000 for the local network on ALL profiles (Domain/Private/Public),
# limited to the local subnet only. Public is included because many office PCs are
# wrongly classed as "Public network" - that is the usual reason other PCs cannot connect.
Remove-NetFirewallRule -DisplayName "SGIC DMS TCP 5000*" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "SGIC DMS TCP 5000 (LAN)" `
  -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow `
  -Profile Any -RemoteAddress LocalSubnet -ErrorAction Stop
Write-Host "Firewall rule created: TCP 5000, local subnet only, all network profiles."
Write-Host "Now test from another PC:  http://<this-PC-IPv4>:5000   (find it with: ipconfig)"
