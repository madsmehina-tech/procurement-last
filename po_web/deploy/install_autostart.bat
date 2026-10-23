@echo off
rem PO Web - start the server automatically after you sign in to Windows. RUN AS ADMINISTRATOR (right-click, Run as administrator).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autostart.ps1"
if errorlevel 1 (
  echo.
  echo FAILED. Right-click this file and choose "Run as administrator".
)
pause
