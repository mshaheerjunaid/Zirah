@echo off
rem Flashes firmware\zirah-devebox.bin onto the DevEBox STM32F4XX_M board
rem through its built-in serial bootloader and the USB-TTL adapter (CP2102).
rem The ML-DSA key in flash sector 7 is not erased.
call "%~dp0zirah.bat" flash --board devebox %*
pause
