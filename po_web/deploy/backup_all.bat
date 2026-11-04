@echo off
rem PO Web - ONE backup of the database AND the archive files together (schedule it daily in Task Scheduler).
rem The destination is [storage] backup_archive_dir in config.ini (put it on ANOTHER disk or PC). The first run copies the whole archive (slow);
rem the next runs copy only what is new. Nothing is deleted in the destination.
chcp 65001 >nul
set "PO_PY=python"
if exist "%~dp0python_path.txt" set /p PO_PY=<"%~dp0python_path.txt"
cd /d "%~dp0.."
"%PO_PY%" tools\backup_all.py
if errorlevel 1 echo [!!] The backup reported a problem - read the lines above.
pause
