@echo off
rem ---------------------------------------------------------------------------
rem  Mapify with a visible console - use this when something is wrong.
rem
rem  Shows model loading, analysis timings and any traceback, which the normal
rem  launcher hides. Pass --debug to open the WebView developer tools as well.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Mapify is not set up. Run: powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m mapify --log-level info %*
echo.
echo Mapify exited with code %ERRORLEVEL%.
pause
endlocal
