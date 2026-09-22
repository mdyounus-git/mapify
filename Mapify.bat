@echo off
rem ---------------------------------------------------------------------------
rem  Mapify - image geolocation reconnaissance
rem
rem  Double-click to open the application.
rem
rem  Run scripts\setup_env.ps1 first if you have not already; it creates the
rem  .venv and downloads the open geodata.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo.
  echo   Mapify is not set up yet.
  echo.
  echo   Run this once, from this folder:
  echo       powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
  echo.
  pause
  exit /b 1
)

rem pythonw.exe keeps the console window from flashing up behind the app.
start "" ".venv\Scripts\pythonw.exe" -m mapify %*
endlocal
