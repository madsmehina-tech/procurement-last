@echo off
rem PO Web - run the TEST copy (staging) next to the real system: its own config file, database copy, archive folder and port.
chcp 65001 >nul
set "PO_PY=python"
if exist "%~dp0python_path.txt" set /p PO_PY=<"%~dp0python_path.txt"
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist config.staging.ini (
  echo config.staging.ini was not found. Copy config.ini to config.staging.ini and change: database path, archive_root, port=5001, environment=staging
  pause
  exit /b 1
)
set PO_CONFIG=%CD%\config.staging.ini
title PO Web - STAGING
"%PO_PY%" serve.py
pause
