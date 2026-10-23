@echo off
rem PO Web - start the server in this window (close the window or press Ctrl+C to stop).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
title PO Web Server
python serve.py
echo.
echo The server stopped. Press any key to close.
pause >nul
