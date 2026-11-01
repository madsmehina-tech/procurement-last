@echo off
rem PO Web - ONE backup of the database AND the archive files together (schedule it daily in Task Scheduler).
rem The destination is [storage] backup_archive_dir in config.ini (put it on ANOTHER disk or PC). The first run copies the whole archive (slow);
rem the next runs copy only what is new. Nothing is deleted in the destination.
chcp 65001 >nul
cd /d "%~dp0.."
python tools\backup_all.py
if errorlevel 1 echo [!!] The backup reported a problem - read the lines above.
pause
