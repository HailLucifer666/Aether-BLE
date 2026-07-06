@echo off
setlocal

set "ROOT=%~dp0"
set "BRIDGE_DIR=%ROOT%aether-bridge"
set "DASH_DIR=%ROOT%aether-dashboard"

rem ---------------------------------------------------------------------------
rem Aether Protocol - combined launcher.
rem
rem Starts everything at once so you can flip "Source" in the dashboard
rem without relaunching a different .bat:
rem   - Real BLE bridge.py on :8765 (Live BLE - needs a real beacon nearby)
rem   - Scanner-A / Scanner-B simulated scanners on :9001 / :9002
rem   - The mesh aggregator on :8766 (peers only the two simulated scanners)
rem   - The dashboard (:3000)
rem Aether.bat and AetherMesh.bat still work standalone; this just runs both
rem sets of backends side by side since their ports never collide.
rem ---------------------------------------------------------------------------

where wt.exe >nul 2>nul
if errorlevel 1 goto :fallback

echo Starting Aether Protocol (bridge + mesh + dashboard) in one Windows Terminal window (5 panes)...

wt.exe -w -1 new-tab --title "Aether Bridge" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe bridge.py" ; split-pane -V --title "Scanner-A" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-A --port 9001 --base-rssi -55 --script=-50@15,-70@15,-50@15 --noise 1.5 --seed 1" ; split-pane -H --title "Scanner-B" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-B --port 9002 --base-rssi -70 --script=-70@15,-50@15,-70@15 --noise 1.5 --seed 2" ; split-pane -H --title "Aggregator" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe aggregator.py --peers ws://127.0.0.1:9001,ws://127.0.0.1:9002" ; split-pane -H --title "Aether Dashboard" -d "%DASH_DIR%" cmd /k "npm.cmd run dev"

timeout /t 5 /nobreak >nul
start http://localhost:3000
exit /b 0

:fallback
echo Windows Terminal (wt.exe) was not found - falling back to separate windows.

start "Aether Bridge (BLE)" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe bridge.py"
start "Scanner-A" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-A --port 9001 --base-rssi -55 --script=-50@15,-70@15,-50@15 --noise 1.5 --seed 1"
start "Scanner-B" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-B --port 9002 --base-rssi -70 --script=-70@15,-50@15,-70@15 --noise 1.5 --seed 2"
timeout /t 1 /nobreak >nul
start "Aggregator" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe aggregator.py --peers ws://127.0.0.1:9001,ws://127.0.0.1:9002"
start "Dashboard" cmd /k "cd /d "%DASH_DIR%" && npm.cmd run dev"

timeout /t 5 /nobreak >nul
start http://localhost:3000

echo.
echo Everything is running. In the dashboard header, toggle "Source"
echo to switch between Live BLE (real bridge.py) and Mesh (simulated
echo Scanner-A/B + aggregator). If the bridge window shows a "port
echo already in use" error, an old bridge is still running - close
echo that window first, then re-run this file.
echo.
pause
