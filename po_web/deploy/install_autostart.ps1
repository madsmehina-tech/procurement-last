# PO Web - creates the "PO Web Server" scheduled task. Run through install_autostart.bat (as administrator).
#  * starts 30 seconds after YOU sign in to Windows (Microsoft Word, used for the contract PDFs, needs a signed-in user);
#  * never stopped after 3 days (the default setting would stop it), also not when the PC runs on battery;
#  * started again by Windows if it fails; not run with extra (administrator) rights.
$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot 'start_server_loop.bat'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'PO Web Server' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
Write-Host 'Done. The server will start by itself 30 seconds after you sign in, and Windows restarts it if it fails.'
Write-Host 'To remove: remove_autostart.bat     To stop it cleanly before shutting down: stop_server.bat'
