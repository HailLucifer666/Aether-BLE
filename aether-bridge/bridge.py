"""Aether Protocol Phase 1 - real-time BLE proximity bridge.

Scans continuously for a BLE beacon by advertised local name (never by MAC
address - Android rotates the BLE MAC per advertising session), smooths the
RSSI with an EMA, detects "beacon lost" transitions, and broadcasts state
over a WebSocket server for a dashboard to consume. Also prints a live
single-line terminal readout so this script is a legitimate standalone demo
with no browser connected.
"""

import argparse
import asyncio
import json
import signal
import sys
from dataclasses import dataclass

import websockets
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from messages import build_lost_message, build_reading_message
from smoothing import apply_ema

DEFAULT_TARGET_NAME = "OnePlus 7T"
DEFAULT_SCANNER_ID = "PC"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Windows' WinRT BLE scanning stack uses a hardcoded ~118ms scan interval with
# an ~18ms scan window (~15% duty cycle) that no application can adjust, and
# this gets worse with several concurrently-advertising nearby BLE devices
# competing for that same narrow window. Research into an analogous setup
# (single beacon, similar advertising interval, same Windows scanning
# constraint) measured real-world reception gaps up to ~9s even with fully
# healthy hardware. The original 3.0s default was far tighter than that
# measured gap ceiling and caused spurious lost/reacquire flapping. 6.0s is
# chosen as a middle ground: comfortably above the multi-second gaps this
# platform is known to produce in normal operation, while still noticeably
# shorter than the ~8-9s worst case so a genuinely lost beacon (screen
# locked, app killed, walked out of range) is still flagged within a demo-
# reasonable window rather than staying silently "live" for too long.
DEFAULT_LOST_THRESHOLD_SECONDS = 6.0
LOST_CHECK_INTERVAL_SECONDS = 0.5
BROADCAST_INTERVAL_SECONDS = 0.4

# Any-advertisement heartbeat: if the scanner hasn't observed a SINGLE
# advertisement from ANY device (not just the target) for this long, the
# WinRT watcher itself is presumed stalled/dead (a documented class of
# Windows BLE issue), as opposed to the target simply not advertising right
# now. This is intentionally set relative to the (possibly custom) lost
# threshold rather than as a bare constant, so it stays meaningfully longer
# than normal per-beacon miss jitter no matter what --lost-threshold is set
# to.
SCANNER_STALL_GRACE_SECONDS = 5.0
SCANNER_STALL_CHECK_INTERVAL_SECONDS = 1.0

BAR_WIDTH = 40
BAR_MIN_RSSI = -100
BAR_MAX_RSSI = -30


def _raw_name(device: BLEDevice, adv: AdvertisementData) -> str | None:
    if adv.local_name:
        return adv.local_name
    if device.name:
        return device.name
    return None


@dataclass
class BeaconState:
    """Mutable tracking state for the target beacon.

    Not a wire-format object - internal only. See build_reading_message /
    build_lost_message for the JSON contract sent to clients.
    """

    raw_rssi: float | None = None
    smoothed_rssi: float | None = None
    last_seen_monotonic: float | None = None
    is_lost: bool = False


def render_terminal_line(
    name: str,
    raw_rssi: float | None,
    smoothed_rssi: float | None,
    is_lost: bool,
    lost_threshold_seconds: float,
) -> str:
    if is_lost or raw_rssi is None or smoothed_rssi is None:
        return f"\r[{name}] LOST - no advertisement in the last {lost_threshold_seconds:.1f}s" + " " * 20

    clamped = max(BAR_MIN_RSSI, min(BAR_MAX_RSSI, smoothed_rssi))
    fraction = (clamped - BAR_MIN_RSSI) / (BAR_MAX_RSSI - BAR_MIN_RSSI)
    filled = int(round(fraction * BAR_WIDTH))
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)
    return f"\r[{name}] raw={raw_rssi:6.1f} dBm  smoothed={smoothed_rssi:6.1f} dBm  [{bar}]  "


class Bridge:
    def __init__(
        self,
        target_name: str,
        scanner_id: str,
        host: str,
        port: int,
        lost_threshold_seconds: float = DEFAULT_LOST_THRESHOLD_SECONDS,
    ) -> None:
        self.target_name = target_name
        self.target_name_lower = target_name.lower()
        self.scanner_id = scanner_id
        self.host = host
        self.port = port
        self.lost_threshold_seconds = lost_threshold_seconds

        self.state = BeaconState()
        self.clients: set[websockets.asyncio.server.ServerConnection] = set()
        self._latest_message: dict = build_lost_message(scanner_id, target_name)
        self._dirty = False
        self._stop_event = asyncio.Event()

        # Any-advertisement heartbeat, independent of target-name matching.
        # Updated on EVERY callback invocation (before the name filter) so it
        # reflects whether the underlying WinRT watcher is delivering
        # anything at all, not just whether the target beacon is in range.
        self._last_any_advertisement_monotonic: float | None = None
        self._scanner: BleakScanner | None = None
        self._scanner_restart_count = 0

    # -- BLE scanning -----------------------------------------------------

    def _on_advertisement(self, device: BLEDevice, adv: AdvertisementData) -> None:
        self._last_any_advertisement_monotonic = asyncio.get_running_loop().time()

        raw = _raw_name(device, adv)
        if raw is None or raw.lower() != self.target_name_lower:
            return

        now = self._last_any_advertisement_monotonic
        self.state.raw_rssi = float(adv.rssi)
        self.state.smoothed_rssi = apply_ema(self.state.smoothed_rssi, float(adv.rssi))
        self.state.last_seen_monotonic = now
        self.state.is_lost = False
        self._dirty = True

    async def _lost_watchdog(self) -> None:
        """Periodically checks whether the beacon has gone silent.

        Emits exactly one "lost" transition (not repeated every tick) and
        clears back to normal once the beacon reappears.
        """
        loop = asyncio.get_running_loop()
        while not self._stop_event.is_set():
            await asyncio.sleep(LOST_CHECK_INTERVAL_SECONDS)
            if self.state.last_seen_monotonic is None:
                continue
            elapsed = loop.time() - self.state.last_seen_monotonic
            if elapsed > self.lost_threshold_seconds and not self.state.is_lost:
                self.state.is_lost = True
                self._dirty = True

    async def _scanner_stall_watchdog(self) -> None:
        """Detects a stalled/dead WinRT advertisement watcher and self-heals.

        Distinct from `_lost_watchdog`: this checks whether ANY advertisement
        (from any nearby device) has been seen recently, not just the target
        beacon. A healthy scanner in a room with other BLE devices should
        always be seeing something; if it sees literally nothing for an
        extended window, that indicates the underlying OS watcher itself has
        stalled (a documented Windows/bleak failure mode) rather than the
        target simply being out of range or asleep. When that happens we
        proactively stop and recreate the BleakScanner instead of waiting on
        a dead scanner forever.
        """
        stall_window = self.lost_threshold_seconds + SCANNER_STALL_GRACE_SECONDS
        loop = asyncio.get_running_loop()
        while not self._stop_event.is_set():
            await asyncio.sleep(SCANNER_STALL_CHECK_INTERVAL_SECONDS)
            if self._last_any_advertisement_monotonic is None:
                continue
            elapsed = loop.time() - self._last_any_advertisement_monotonic
            if elapsed > stall_window:
                await self._restart_scanner(elapsed)

    async def _restart_scanner(self, stalled_for_seconds: float) -> None:
        if self._scanner is None:
            return
        self._scanner_restart_count += 1
        print(
            f"\n[bridge] WARNING: no BLE advertisements from ANY device for "
            f"{stalled_for_seconds:.1f}s - WinRT scanner appears stalled. "
            f"Restarting scanner (restart #{self._scanner_restart_count})..."
        )
        try:
            await self._scanner.stop()
        except Exception as exc:  # noqa: BLE001 - log and continue; recreate regardless
            print(f"[bridge] WARNING: error stopping stalled scanner: {exc}")

        self._scanner = BleakScanner(detection_callback=self._on_advertisement)
        # Treat the moment of restart as a fresh heartbeat so the watchdog
        # doesn't immediately re-trigger while the new watcher spins up.
        self._last_any_advertisement_monotonic = asyncio.get_running_loop().time()
        try:
            await self._scanner.start()
            print("[bridge] Scanner restarted successfully.")
        except Exception as exc:  # noqa: BLE001 - surface but keep running
            print(f"[bridge] ERROR: failed to restart scanner: {exc}")

    # -- WebSocket server ---------------------------------------------------

    def _current_message(self) -> dict:
        if self.state.is_lost or self.state.last_seen_monotonic is None:
            return build_lost_message(self.scanner_id, self.target_name)

        loop = asyncio.get_running_loop()
        last_seen_ms = int(max(0.0, (loop.time() - self.state.last_seen_monotonic) * 1000))
        return build_reading_message(
            self.scanner_id,
            self.target_name,
            self.state.raw_rssi,
            self.state.smoothed_rssi,
            last_seen_ms,
        )

    async def _handle_client(self, websocket) -> None:
        self.clients.add(websocket)
        try:
            # Acceptance criterion: a newly connected client must receive
            # the current state immediately, not wait for the next tick.
            await websocket.send(json.dumps(self._current_message()))
            async for _ in websocket:
                pass  # this bridge is broadcast-only; inbound messages are ignored
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)

    async def _broadcast_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
            message = self._current_message()
            self._latest_message = message
            if not self.clients:
                continue
            payload = json.dumps(message)
            stale = []
            for client in self.clients:
                try:
                    await client.send(payload)
                except websockets.exceptions.ConnectionClosed:
                    stale.append(client)
            for client in stale:
                self.clients.discard(client)

    # -- Terminal readout ---------------------------------------------------

    async def _terminal_readout_loop(self) -> None:
        while not self._stop_event.is_set():
            line = render_terminal_line(
                self.target_name,
                self.state.raw_rssi,
                self.state.smoothed_rssi,
                self.state.is_lost,
                self.lost_threshold_seconds,
            )
            sys.stdout.write(line)
            sys.stdout.flush()
            await asyncio.sleep(0.2)

    # -- Lifecycle ------------------------------------------------------------

    async def run(self) -> None:
        self._scanner = BleakScanner(detection_callback=self._on_advertisement)
        await self._scanner.start()
        self._last_any_advertisement_monotonic = asyncio.get_running_loop().time()

        server = await websockets.serve(self._handle_client, self.host, self.port)
        print(f"[bridge] WebSocket server listening on ws://{self.host}:{self.port}")
        print(f"[bridge] Scanning for local name == {self.target_name!r} as scanner {self.scanner_id!r}.")
        print(f"[bridge] Lost threshold: {self.lost_threshold_seconds:.1f}s.")
        print("[bridge] Press Ctrl+C to stop.\n")

        watchdog_task = asyncio.ensure_future(self._lost_watchdog())
        stall_watchdog_task = asyncio.ensure_future(self._scanner_stall_watchdog())
        broadcast_task = asyncio.ensure_future(self._broadcast_loop())
        readout_task = asyncio.ensure_future(self._terminal_readout_loop())

        try:
            await self._stop_event.wait()
        finally:
            watchdog_task.cancel()
            stall_watchdog_task.cancel()
            broadcast_task.cancel()
            readout_task.cancel()
            for task in (watchdog_task, stall_watchdog_task, broadcast_task, readout_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if self._scanner is not None:
                await self._scanner.stop()

            for client in list(self.clients):
                await client.close()
            server.close()
            await server.wait_closed()
            print()  # move off the readout line

    def stop(self) -> None:
        self._stop_event.set()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aether Protocol real-time BLE proximity bridge.")
    parser.add_argument("--name", default=DEFAULT_TARGET_NAME, help=f"Target BLE local name (default: {DEFAULT_TARGET_NAME!r}).")
    parser.add_argument("--scanner", default=DEFAULT_SCANNER_ID, help=f"Identifier for this physical scanning machine (default: {DEFAULT_SCANNER_ID!r}).")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"WebSocket bind host (default: {DEFAULT_HOST}).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"WebSocket bind port (default: {DEFAULT_PORT}).")
    parser.add_argument(
        "--lost-threshold",
        type=float,
        default=DEFAULT_LOST_THRESHOLD_SECONDS,
        help=(
            "Seconds without an advertisement from the target before it is reported "
            f"'lost' (default: {DEFAULT_LOST_THRESHOLD_SECONDS:.1f}). Windows' BLE scanning "
            "stack has known multi-second reception gaps even when the beacon is healthy; "
            "raise this if you still see spurious lost/reacquire flapping."
        ),
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    bridge = Bridge(args.name, args.scanner, args.host, args.port, args.lost_threshold)

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, bridge.stop)
        await bridge.run()
    else:
        # signal.SIGINT handlers via add_signal_handler are unsupported on
        # Windows' asyncio event loop; rely on KeyboardInterrupt instead.
        try:
            await bridge.run()
        except KeyboardInterrupt:
            bridge.stop()


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[bridge] Stopped by user.")


if __name__ == "__main__":
    main()
