@echo off
rem Zirah setup: run once. Installs everything into a private folder (.venv)
rem inside the Zirah folder, so nothing else on this PC is touched, then
rem adds Zirah to the Start Menu (and the Desktop if you want).
setlocal EnableExtensions
title Zirah Setup
cd /d "%~dp0"

echo.
echo   Setting up Zirah in "%CD%"
echo.

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY goto nopython

if exist ".venv\Scripts\python.exe" goto havevenv
echo   Creating the private Python environment...
%PY% -m venv .venv
if errorlevel 1 goto venvfail
:havevenv

echo   Installing packages (PySide6 is large, this can take a few minutes)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "host\requirements.txt" -r "ui\requirements.txt"
if errorlevel 1 goto pipfail
".venv\Scripts\python.exe" -c "import PySide6, serial, dilithium_py, cryptography" >nul 2>&1
if errorlevel 1 goto pipfail

echo   Adding Zirah to the Start Menu...
set "ZIRAH_DIR=%CD%"
set "ZIRAH_WHERE=start"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\shortcut.ps1"

choice /C YN /N /M "  Also put a Zirah shortcut on the Desktop? [Y/N] "
if errorlevel 2 goto done
set "ZIRAH_WHERE=desktop"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\shortcut.ps1"

:done
echo.
echo   Done. Start Zirah from the Start Menu, or double-click zirah-app.bat.
echo   To flash the firmware, plug the mini-USB cable into the ST-LINK and run flash.bat.
echo.
pause
exit /b 0

:nopython
echo   Python 3.10 or newer was not found.
echo   Install Python from python.org, tick "Add python.exe to PATH" on the
echo   first screen, then run setup.bat again.
pause
exit /b 1

:venvfail
echo   Could not create the private environment in "%CD%\.venv".
echo   Check that this folder is writable (not inside a read-only zip).
pause
exit /b 1

:pipfail
echo   Installing the packages failed. Check the internet connection and
echo   run setup.bat again. The messages above say which package failed.
pause
exit /b 1
