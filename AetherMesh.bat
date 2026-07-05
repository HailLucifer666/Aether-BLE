@echo off
setlocal

set "ROOT=%~dp0"
set "BRIDGE_DIR=%ROOT%aether-bridge"
set "DASH_DIR=%ROOT%aether-dashboard"

rem ---------------------------------------------------------------------------
rem Aether Protocol - multi-scanner mesh demo launcher.
rem
rem Starts two scanners (Scanner-A on :9001, Scanner-B on :9002), the mesh
rem aggregator (:8766), and the dashboard (:3000) in one Windows Terminal
rem window split into 4 panes. Opens the dashboard in the browser.
rem
rem IMPORTANT for cmd + wt.exe interop: each pane command is written inline
rem (not via %VAR% expansion) and uses --script=VALUE so argparse treats the
rem dash-prefixed ramp value as an argument, not an unknown flag.
rem ---------------------------------------------------------------------------

where wt.exe >nul 2>nul
if errorlevel 1 goto :fallback

echo Starting Aether Protocol mesh in one Windows Terminal window (4 panes)...

wt.exe -w -1 new-tab --title "Scanner-A" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-A --port 9001 --base-rssi -55 --script=-50@15,-70@15,-50@15 --noise 1.5 --seed 1" ; split-pane -V --title "Scanner-B" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-B --port 9002 --base-rssi -70 --script=-70@15,-50@15,-70@15 --noise 1.5 --seed 2" ; split-pane -H --title "Aggregator" -d "%BRIDGE_DIR%" cmd /k ".venv\Scripts\python.exe aggregator.py --peers ws://127.0.0.1:9001,ws://127.0.0.1:9002" ; split-pane -H --title "Aether Dashboard" -d "%DASH_DIR%" cmd /k "npm.cmd run dev"

timeout /t 5 /nobreak >nul
start http://localhost:3000
exit /b 0

:fallback
echo Windows Terminal (wt.exe) was not found - falling back to separate windows.

start "Scanner-A" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-A --port 9001 --base-rssi -55 --script=-50@15,-70@15,-50@15 --noise 1.5 --seed 1"
start "Scanner-B" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe simulated_scanner.py --scanner Scanner-B --port 9002 --base-rssi -70 --script=-70@15,-50@15,-70@15 --noise 1.5 --seed 2"
timeout /t 1 /nobreak >nul
start "Aggregator" cmd /k "cd /d "%BRIDGE_DIR%" && .venv\Scripts\python.exe aggregator.py --peers ws://127.0.0.1:9001,ws://127.0.0.1:9002"
start "Dashboard" cmd /k "cd /d "%DASH_DIR%" && npm.cmd run dev"

timeout /t 5 /nobreak >nul
start http://localhost:3000

echo.
echo Once the dashboard loads, toggle "Source" to "Mesh" in the header.
echo Scanner-A starts as owner and walks away over ~15s while Scanner-B
echo walks closer - watch the owner spotlight hand off mid-conversation.
echo.
pause
