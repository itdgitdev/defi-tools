@echo off
setlocal
title Binance Futures Monitor
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_binance_futures_monitor.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Monitor stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
