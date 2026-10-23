@echo off
rem PO Web - start the server automatically 30 seconds after you sign in to Windows. RUN AS ADMINISTRATOR.
cd /d "%~dp0"
schtasks /Create /TN "PO Web Server" /TR "wscript.exe \"%~dp0start_server_hidden.vbs\"" /SC ONLOGON /DELAY 0000:30 /RL HIGHEST /F
if errorlevel 1 (
  echo.
  echo FAILED. Right-click this file and choose "Run as administrator".
) else (
  echo.
  echo Done. The server will start by itself 30 seconds after you sign in. To remove: remove_autostart.bat
)
pause
