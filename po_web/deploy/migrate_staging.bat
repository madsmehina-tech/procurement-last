@echo off
rem PO Web - bring the STAGING database up to date (uses config.staging.ini).
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
set PO_CONFIG=%CD%\config.staging.ini
python migrate.py
pause
