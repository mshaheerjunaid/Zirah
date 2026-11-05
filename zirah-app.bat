@echo off
rem Starts the Zirah desktop app. The window has no console behind it:
rem this script hands over to pythonw and closes at once.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto nosetup
if not exist "ui\zirah_app.py" goto broken
start "" ".venv\Scripts\pythonw.exe" "ui\zirah_app.py" %*
exit /b 0

:nosetup
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('Zirah is not set up on this PC yet. Run setup.bat in the Zirah folder first.', 'Zirah', 'OK', 'Information')"
exit /b 1

:broken
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('The ui folder is missing. Extract the whole Zirah folder again.', 'Zirah', 'OK', 'Error')"
exit /b 1
