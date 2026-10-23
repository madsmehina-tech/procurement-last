@echo off
rem PO Web - stop the server that is listening on the configured port (default 5000).
set PORT=5000
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force; 'Stopped process ' + $_.OwningProcess }"
echo Done.
pause
