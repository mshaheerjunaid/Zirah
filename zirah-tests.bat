@echo off
rem Runs every Zirah test suite and prints PASS, FAIL or SKIP for each.
if not exist "%~dp0.venv\Scripts\python.exe" goto nopy
"%~dp0.venv\Scripts\python.exe" "%~dp0tests\run_tests.py" %*
goto end
:nopy
py -3 "%~dp0tests\run_tests.py" %*
:end
pause
