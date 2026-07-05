@echo off
setlocal

set "ROOT=%~dp0"
set "BRIDGE_DIR=%ROOT%aether-bridge"
set "DASH_DIR=%ROOT%aether-dashboard"

rem ---------------------------------------------------------------------------
rem Phase 2 multi-device mesh demo - no BLE hardware required.
rem
rem Launches two simulated scanners (SIM-A, SIM-B) and the mesh aggregator
rem that fuses them and runs leader election, plus the dashboard. SIM-A
rem starts "closer" (-55 dBm) and walks away over ~15s while SIM-B walks
rem closer (-70 -> -50 dBm), so within ~30s the dashboard shows a real
rem ownership handoff with hysteresis (toggle Source -> Mesh in the header).
rem
rem This is separate from Aether.bat, which launches the single-scanner
rem real-hardware bridge. The two never run at the same time (both use
rem ws://127.0.0.1:8765 / :8766 / :9001 / :9002).
rem ---------------------------------------------------------------------------

set "SIM_A=simulated_scanner.py --scanner SIM-A --port 9001 --base-rssi -55 --script -50@15,-70@15,-50@15 --noise 1.5 --seed 1"
set "SIM_B=simulated_scanner.py --scanner SIM-B --port 9002 --base-rssi -70 --script -70@15,-50@15,-70@15 --noise 1.5 --seed 2"
set "AGGREGATOR=aggregator.py --peers ws://127.0.0.1:9001,ws://127.0.0.1:9002"

where wt.exe >nul 2>nul
if errorlevel 1 goto :fallback

echo Starting Aether Protocol mesh demo in one Windows Terminal window (4 panes)...

wt.exe -w -1 new-tab --title "SIM-A" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe %SIM_A%" ; split-pane -V --title "SIM-B" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe %SIM_B%" ; split-pane -H --title "Aggregator" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe %AGGREGATOR%" ; split-pane -H --title "Aether Dashboard" -d "%DASH_DIR%" cmd /k "npm.cmd run dev"

timeout /t 5 /nobreak >nul
start http://localhost:3000
exit /b 0

:fallback
echo Windows Terminal (wt.exe) was not found - falling back to
echo separate windows instead of a split view.
echo.

start "SIM-A (simulated scanner)" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe %SIM_A%"
start "SIM-B (simulated scanner)" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe %SIM_B%"
timeout /t 1 /nobreak >nul
start "Mesh Aggregator" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe %AGGREGATOR%"
start "Aether Dashboard (Next.js)" cmd /k "cd /d "%DASH_DIR%" && npm.cmd run dev"

timeout /t 5 /nobreak >nul
start http://localhost:3000

echo.
echo Done. Once the dashboard loads, toggle "Source" to "Mesh" in the header.
echo SIM-A starts closer (owning the conversation) and walks away around the
echo 15s mark while SIM-B walks closer - watch the owner spotlight hand off.
echo.
echo Close all four windows when finished.
echo.
pause
