@echo off
rem PO Web - let the other PCs (local network) and your Tailscale devices reach the server. RUN AS ADMINISTRATOR.
rem Change PORT here if you changed it in config.ini.
set PORT=5000
net session >nul 2>&1
if errorlevel 1 (
  echo Run this file as administrator: right-click it and choose "Run as administrator".
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="PO Web (LAN)" >nul 2>&1
netsh advfirewall firewall delete rule name="PO Web (Tailscale)" >nul 2>&1
rem the office network only (private / domain profiles, same subnet)
netsh advfirewall firewall add rule name="PO Web (LAN)" dir=in action=allow protocol=TCP localport=%PORT% profile=private,domain remoteip=LocalSubnet
rem Tailscale devices only (100.64.0.0/10) - nothing else from the internet
netsh advfirewall firewall add rule name="PO Web (Tailscale)" dir=in action=allow protocol=TCP localport=%PORT% remoteip=100.64.0.0/10
echo.
echo Done. Port %PORT% is open for the local network and for Tailscale only.
echo If the other PCs still cannot connect: make sure this PC's network is set to "Private" (Settings - Network).
pause
