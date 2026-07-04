# Aether Protocol BLE Bridge

A real-time Bluetooth Low Energy (BLE) scanner that broadcasts beacon proximity data over WebSocket to a dashboard. This bridge continuously scans for a target BLE beacon by advertised local name, smooths the signal strength (RSSI) with exponential moving average, and streams live readings to connected clients.

## What this is

This is **not** a simulation. The bridge scans actual BLE advertisements from a physical phone running nRF Connect (or any compatible BLE advertiser). It:

1. Matches beacons by advertised local name (never by MAC address — Android rotates MAC per session)
2. Smooths RSSI with EMA (alpha=0.3) to filter RF noise
3. Detects "beacon lost" transitions (after 6 seconds of silence)
4. Broadcasts state via WebSocket (`ws://127.0.0.1:8765`) as JSON
5. Prints a live terminal readout (independent of any browser connection)

## Prerequisites

- Python 3.11+ with venv already configured at `F:\Aether BLE\aether-bridge\.venv`
- `bleak` >= 1.0 and `websockets` >= 14.0 (already installed in venv)
- Windows with Bluetooth enabled (Settings → Bluetooth & devices → Bluetooth: On)
- Target phone running nRF Connect (or equivalent BLE advertiser) advertising as local name `OnePlus 7T`

## Quick start

### 1. Activate the venv

```powershell
F:\Aether BLE\aether-bridge\.venv\Scripts\Activate.ps1
```

If PowerShell throws an execution policy error, run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then re-run the activation script.

### 2. Verify the radio with the diagnostic gate

Before running the bridge, always run `diag.py` in `--mode both`:

```powershell
F:\Aether BLE\aether-bridge\.venv\Scripts\python.exe diag.py --mode both
```

This performs two checks:

- **radio mode** (10 second window) — confirms Windows BLE stack is working at all. If zero advertisements appear, this prints `RADIO/DRIVER/PERMISSIONS ISSUE` and stops. Fix Windows Bluetooth before proceeding.
- **name mode** (continuous, Ctrl+C to stop) — filters to your target beacon by advertised local name. If the radio passed but the name never matches, this prints `NAME MATCH ISSUE` with context.

Only proceed to step 3 if `diag.py` reports `PASS` for both checks.

### 3. Run the bridge

```powershell
F:\Aether BLE\aether-bridge\.venv\Scripts\python.exe bridge.py
```

Expected output:

```
[bridge] WebSocket server listening on ws://127.0.0.1:8765
[bridge] Scanning for local name == 'OnePlus 7T' as scanner 'PC'.
[bridge] Press Ctrl+C to stop.

[OnePlus 7T] raw= -60.0 dBm  smoothed= -60.0 dBm  [████████████████░░░░░░░░░░░░░░░░░░░░░░]
```

The terminal line updates every 200 ms with raw RSSI, smoothed RSSI, and a relative strength bar. If the beacon goes silent for >6 seconds, the line reads:

```
[OnePlus 7T] LOST - no advertisement in the last 6.0s
```

### 4. Open the dashboard (in a separate terminal)

With the bridge running, open `F:\Aether BLE\aether-dashboard` and run:

```powershell
npm.cmd run dev
```

Then open `http://localhost:3000` and toggle "Live BLE" in the header. The dashboard connects to the bridge's WebSocket and displays:

- Connection status (Connecting / Live / Not Connected / Signal Lost)
- Real-time RSSI sparkline
- Current ownership state (which device owns the conversation)
- "Calibrate @ 1m" button for distance estimation

## CLI flags

### `diag.py`

```
--mode {radio,name,both}  Which diagnostic step to run (default: both)
--name TEXT               Target BLE local name to match (default: OnePlus 7T)
--window SECONDS          Radio scan window duration (default: 10.0)
```

Examples:

```powershell
# Run both checks (recommended)
python.exe diag.py --mode both

# Scan for any BLE advertisement for 5 seconds
python.exe diag.py --mode radio --window 5.0

# Continuously filter to a custom beacon name
python.exe diag.py --mode name --name CustomBeaconName
```

### `bridge.py`

```
--name TEXT    Target BLE local name (default: OnePlus 7T)
--scanner TEXT Identifier for this machine (default: PC)
--host TEXT    WebSocket bind host (default: 127.0.0.1)
--port INT     WebSocket bind port (default: 8765)
```

Examples:

```powershell
# Default: scan for OnePlus 7T, serve on ws://127.0.0.1:8765
python.exe bridge.py

# Custom beacon name
python.exe bridge.py --name MyBeacon

# Bind to a specific interface (e.g., allow LAN connections)
python.exe bridge.py --host 192.168.1.100 --port 9000
```

## nRF Connect phone setup

On your Android phone (tested with OnePlus 7T):

1. Install [nRF Connect](https://play.google.com/store/apps/details?id=no.nordicsemi.android.mcp)
2. Open the app → **Advertiser** tab
3. Create a new advertisement:
   - **Local name:** `OnePlus 7T` (exactly this, case-insensitive matching, but the casing you set here will appear in logs)
   - **TX Power:** 0 dBm
   - **Advertising interval:** 250 ms
   - **Advertising mode:** Legacy (ensure the name is in the advertising packet itself, not a scan response)
4. **Critical:** Keep the phone screen unlocked during any test. nRF Connect stops advertising when the screen locks — the bridge will report `LOST` immediately.
5. Start the advertisement
6. Confirm the phone is advertising: open nRF Connect's **Scanner** tab and check that your device appears with the correct local name

## Troubleshooting

### `diag.py` reports "RADIO/DRIVER/PERMISSIONS ISSUE"

The Windows Bluetooth stack detected zero advertisements in 10 seconds.

1. **Check Bluetooth is enabled:**
   - Windows Settings → Bluetooth & devices → Bluetooth toggle: **On**
2. **Check Bluetooth adapter drivers:**
   - Device Manager → Bluetooth → your adapter → Driver tab → check for errors or outdated drivers
3. **Check app permissions:**
   - Windows Settings → Privacy & security → App permissions → Bluetooth → confirm the terminal/Python app has permission
4. **Toggle Bluetooth:**
   - Settings → Bluetooth & devices → Bluetooth toggle: **Off**, wait 5s, toggle: **On**
5. **Advanced (only if the above fails):**
   - Try a different Python interpreter (check if the bleak/winrt backend imports successfully)
   - Use `usbipd-win` to forward a USB Bluetooth adapter into WSL2 and run `bridge.py` there
   - Use a spare Linux machine or Raspberry Pi as the scanner
   - Use a dedicated BLE scanner (e.g., cheap ESP32 with the nRF5340 DK sketches, or a dedicated BLE dongle on Linux)

### `diag.py` radio passes but name mode reports "NAME MATCH ISSUE"

The Windows Bluetooth stack is working (radio is fine) but no device advertised the expected local name.

1. **Confirm nRF Connect is advertising:**
   - On the phone, open nRF Connect → Advertiser tab → check status is "Advertising"
   - Look at the Scanner tab to confirm your phone appears with the correct local name
2. **Confirm the name matches exactly:**
   - In `diag.py` output, look at the actual raw names seen: `raw_name='...'`
   - If your name is `MyBeacon` but `diag.py` searches for `OnePlus 7T`, run: `python.exe diag.py --mode name --name MyBeacon`
3. **Check phone screen:**
   - Unlock the phone screen. nRF Connect pauses advertising when the screen locks.
4. **Check advertising mode:**
   - In nRF Connect, verify you selected **Legacy** advertising mode (not BLE 5.x extended). The name must appear in the advertising packet, not scan response.
5. **Try a passive scan mode (if available in a future release):**
   - Currently not implemented; reserved for future BLE stack tuning.

### Bridge runs but dashboard shows "Not Connected" / "Signal Lost"

The bridge is running locally but the dashboard cannot reach the WebSocket or the beacon is out of range.

1. **Confirm the bridge is running:**
   - Check the terminal output: `[bridge] WebSocket server listening on ws://127.0.0.1:8765`
2. **Check the dashboard is connecting to the right URL:**
   - Dashboard source is hard-coded to `ws://127.0.0.1:8765`; if you changed `--host` or `--port`, rebuild the dashboard with the new address
3. **Confirm the beacon is in range:**
   - Look at the bridge terminal readout; if it shows `LOST`, the beacon is out of BLE range or the phone screen locked
4. **Check firewall:**
   - Windows Defender Firewall → Allow an app through firewall → ensure Python is allowed for private networks

### Dashboard reports "SIGNAL LOST" instead of a distance estimate

This is **not** an error. "SIGNAL LOST" means the bridge has not seen an advertisement from the target beacon for >6 seconds. This can happen because:

- The phone screen locked (nRF Connect pauses advertising)
- The phone walked out of BLE range
- The radio lost synchronization briefly

Once the beacon reappears (screen unlocked, back in range), the dashboard will resume showing RSSI and ownership state.

## Architecture notes

- **Matching:** Always by advertised local name (from `AdvertisementData.local_name` or `BLEDevice.name`), never by MAC address
- **RSSI smoothing:** EMA with alpha=0.3 (newer readings weighted 30%, historical average 70%)
- **Lost detection:** Watchdog fires after 6 seconds of no advertisement (configurable via `--lost-threshold`); clears as soon as a new advertisement arrives
- **WebSocket broadcast:** JSON messages (types: `reading`, `lost`) sent every 400 ms or on state change
- **New client behavior:** Immediately receives the current state (no wait for next tick)

## JSON message format

### Reading message (beacon visible)

```json
{
  "type": "reading",
  "scanner": "PC",
  "name": "OnePlus 7T",
  "rssi": -62.5,
  "smoothedRssi": -61.2,
  "lastSeenMs": 125,
  "ts": "14:32:19"
}
```

### Lost message (beacon absent)

```json
{
  "type": "lost",
  "scanner": "PC",
  "name": "OnePlus 7T",
  "ts": "14:32:22"
}
```

## Stopping the bridge

Press **Ctrl+C** in the terminal. The bridge will:

1. Cancel all scanning tasks
2. Close all WebSocket connections
3. Shut down the server
4. Exit cleanly

## Next steps

Once hardware verification is complete (phone runs nRF Connect, diag.py and bridge.py both work, dashboard shows live RSSI and ownership), the next phase is multi-device mesh and leader election.
