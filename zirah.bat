@echo off
rem Zirah command line tool. Examples:  zirah status   zirah sign C:\docs\contract.pdf
if not exist "%~dp0.venv\Scripts\python.exe" goto nopy
"%~dp0.venv\Scripts\python.exe" "%~dp0host\zirah.py" %*
exit /b %errorlevel%
:nopy
py -3 "%~dp0host\zirah.py" %*
