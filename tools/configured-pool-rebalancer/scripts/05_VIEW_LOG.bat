@echo off
setlocal
for %%I in ("%~dp0..") do set "TOOL_ROOT=%%~fI"
set "LOG=%TOOL_ROOT%\runtime\logs\configured_rebalancer_loop.log"
if not exist "%LOG%" (
  echo [FAIL] Log file does not exist yet.
  pause
  exit /b 1
)
powershell.exe -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Wait -Tail 100"
exit /b %ERRORLEVEL%
