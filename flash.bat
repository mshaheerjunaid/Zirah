@echo off
rem Flashes firmware\zirah.bin onto the STM32F4DISCOVERY through its ST-LINK.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\flash.ps1" %*
pause
