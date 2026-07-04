@echo off
setlocal

set "ROOT=%~dp0"
set "BRIDGE_DIR=%ROOT%aether-bridge"
set "DASH_DIR=%ROOT%aether-dashboard"

where wt.exe >nul 2>nul
if errorlevel 1 goto :fallback

echo Starting Aether Protocol in one Windows Terminal window (3 panes)...

wt.exe -w -1 new-tab --title "Aether Bridge" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe bridge.py" ; split-pane -V --title "Aether Dashboard" -d "%DASH_DIR%" cmd /k "npm.cmd run dev" ; split-pane -H --title "Aether Shell" -d "%ROOT%"

timeout /t 5 /nobreak >nul
start http://localhost:3000
exit /b 0

:fallback
echo Windows Terminal (wt.exe) was not found - falling back to
echo separate windows instead of a split view.
echo.

start "Aether Bridge (BLE)" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe bridge.py"
timeout /t 2 /nobreak >nul
start "Aether Dashboard (Next.js)" cmd /k "cd /d "%DASH_DIR%" && npm.cmd run dev"

timeout /t 5 /nobreak >nul
start http://localhost:3000

echo.
echo Done. If the bridge window shows a "port already
echo in use" error, an old bridge is still running -
echo close that window first, then re-run this file.
echo.
pause
