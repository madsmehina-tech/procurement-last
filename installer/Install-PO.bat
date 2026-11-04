@echo off
rem PO Setup - fallback launcher (use PO_Setup.exe if you have it). Put this file, po_setup.py and po_web_v*.zip in ONE folder, then double-click.
rem If Python 3.12 is missing it is installed first (signed installer from python.org, «Add python.exe to PATH» on), then the wizard opens.
chcp 65001 >nul
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  echo Asking for administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
py -3.12 -c "import sys; sys.exit(0 if sys.maxsize>2**32 else 1)" >nul 2>&1
if not errorlevel 1 goto run
echo Installing Python 3.12 (64-bit)...
set "PYEXE=%TEMP%\python-3.12.8-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile $env:PYEXE; $s=Get-AuthenticodeSignature $env:PYEXE; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*Python Software Foundation*'){ Write-Host 'Python installer signature is NOT valid - stopped.'; exit 1 }"
if errorlevel 1 (
  echo Could not get a valid Python installer. Install Python 3.12 (64-bit, tick "Add python.exe to PATH") by hand, then run this file again.
  pause
  exit /b 1
)
"%PYEXE%" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_launcher=1 Include_tcltk=1 Include_test=0 Shortcuts=0
:run
py -3.12 po_setup.py
if errorlevel 1 pause
