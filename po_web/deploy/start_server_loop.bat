@echo off
rem PO Web - runs the server and starts it again if it CRASHES (a clean stop with stop_server.bat ends this window too).
rem Used by the automatic start task. Output goes to logs\server.out.txt
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist logs mkdir logs
set TRIES=0
:run
python serve.py >> logs\server.out.txt 2>&1
if %ERRORLEVEL%==0 goto :end
set /a TRIES+=1
echo [%date% %time%] the server stopped with an error (%ERRORLEVEL%), restart %TRIES% of 20 >> logs\server.out.txt
if %TRIES% GEQ 20 goto :end
timeout /t 15 /nobreak >nul
goto :run
:end
