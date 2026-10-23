# PO Web - CLEAN stop. Asks the server to finish any save that is in progress and then exit by itself.
# Only if it has not stopped after 60 seconds is it ended by force (and you are told).
param([int]$Port = 5000)
$root = Split-Path -Parent $PSScriptRoot
$flag = Join-Path $root 'instance\stop.flag'
function Listening { [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }
if (-not (Listening)) { Write-Host 'The server is not running.'; exit 0 }
New-Item -ItemType Directory -Force -Path (Split-Path $flag) | Out-Null
Set-Content -Path $flag -Value 'stop'
Write-Host 'Asked the server to stop (it finishes any save first) ...'
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    if (-not (Listening)) { Write-Host 'Stopped cleanly.'; exit 0 }
}
Write-Host 'It did not stop within 60 seconds: stopping it by force. Run  python tools\check_storage.py  and  python tools\setup_check.py  before using the system.'
Get-NetTCPConnection -LocalPort $Port -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
Remove-Item -Force -ErrorAction SilentlyContinue $flag
