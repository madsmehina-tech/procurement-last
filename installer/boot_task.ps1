# PO Web - start the server when the PC STARTS, with nobody signed in (a scheduled task that runs as SYSTEM). Run as administrator.
#   .\boot_task.ps1 -Root "D:\...\PO_Setup (1)\app"            install
#   .\boot_task.ps1 -Root "D:\...\PO_Setup (1)\app" -Remove    remove
# Limit: Microsoft Word (contract PDFs) does not work without a signed-in user; the system falls back to LibreOffice if it is installed.
param([Parameter(Mandatory = $true)][string]$Root, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$name = 'PO Web Server (Boot)'
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue; Write-Host 'Removed.'; return }
$bat = Join-Path $Root 'deploy\start_server_loop.bat'
if (-not (Test-Path $bat)) { throw "Not found: $bat  (is -Root the 'app' folder?)" }
Unregister-ScheduledTask -TaskName 'PO Web Server' -Confirm:$false -ErrorAction SilentlyContinue      # the old «after sign-in» task: two servers would fight for the port
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT1M'
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Done. '$name' starts the server 1 minute after the PC starts, with nobody signed in, and Windows restarts it if it fails."
