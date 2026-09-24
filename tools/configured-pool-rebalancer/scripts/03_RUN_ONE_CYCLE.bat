@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_rebalancer.ps1" -Mode Once
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
