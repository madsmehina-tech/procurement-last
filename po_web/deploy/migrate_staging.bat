@echo off
rem PO Web - bring the STAGING database up to date (uses config.staging.ini).
chcp 65001 >nul
set "PO_PY=python"
if exist "%~dp0python_path.txt" set /p PO_PY=<"%~dp0python_path.txt"
set PYTHONUTF8=1
cd /d "%~dp0.."
set PO_CONFIG=%CD%\config.staging.ini
"%PO_PY%" migrate.py
pause
