@echo off
rem PO Web - stop the server cleanly (it finishes any save in progress first). Use this before shutting the PC down.
set PORT=5000
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_server.ps1" -Port %PORT%
pause
