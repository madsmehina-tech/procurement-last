@echo off
rem PO Web - daily backup of the database and the archive. Edit the two paths, then schedule it (Task Scheduler) or run it by hand.
chcp 65001 >nul
set DB=D:\PO System\PurchaseSystem.accdb
set ARCHIVE=D:\PO_Archive
set DEST=E:\PO_Backup
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
if not exist "%DEST%\db" mkdir "%DEST%\db"
copy /Y "%DB%" "%DEST%\db\PurchaseSystem_%TODAY%.accdb" >nul
rem mirrors the archive (copies new/changed files; /XO keeps older backups intact; nothing is deleted at the destination)
robocopy "%ARCHIVE%" "%DEST%\archive" /E /XO /R:2 /W:2 /NFL /NDL /NJH
echo Backup finished: %DEST%
pause
