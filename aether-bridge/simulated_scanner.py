"""Headless simulated BLE scanner - same WS-server interface as bridge.py.

Generates synthetic RSSI (optionally scripted to ramp through segments over
time) instead of scanning real BLE hardware, so the aggregator and election
logic can be demoed and tested end-to-end without any BLE radio present.
Never imports bleak.
"""

import argparse
import asyncio
import json
import random
import sys
from dataclasses import dataclass

import websockets

from messages import build_lost_message, build_reading_message
from smoothing import apply_ema

DEFAULT_SCANNER_ID = "SIM-A"
DEFAULT_TARGET_NAME = "OnePlus 7T"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9001
DEFAULT_BASE_RSSI = -60.0
DEFAULT_NOISE_DB = 2.0

BROADCAST_INTERVAL_SECONDS = 0.4
TICK_INTERVAL_SECONDS = 0.4


@dataclass(frozen=True)
class ScriptSegment:
    """One ramp segment: linearly move the base RSSI to `target_rssi` over `duration_seconds`."""

    target_rssi: float
    duration_seconds: float


def parse_script(script: str) -> list[ScriptSegment]:
    """Parse "rssi@seconds,rssi@seconds,..." into ScriptSegment objects.

    Raises ValueError with a clear message on malformed input - this is
    external (CLI) input and must be validated before use.
    """
    segments = []
    for raw_part in script.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "@" not in part:
            raise ValueError(f"Invalid script segment {part!r}: expected format 'rssi@seconds'.")
        rssi_str, seconds_str = part.split("@", 1)
        try:
            target_rssi = float(rssi_str)
            duration_seconds = float(seconds_str)
        except ValueError as exc:
            raise ValueError(f"Invalid script segment {part!r}: {exc}") from exc
        if duration_seconds < 0:
            raise ValueError(f"Invalid script segment {part!r}: duration must be >= 0.")
        segments.append(ScriptSegment(target_rssi=target_rssi, duration_seconds=duration_seconds))
    if not segments:
        raise ValueError("Script must contain at least one 'rssi@seconds' segment.")
    return segments


class ScriptedRssiSource:
    """Produces the current target base RSSI given elapsed simulated time.

    Ramps linearly through each segment in order, then holds the final
    segment's target RSSI indefinitely once all segments have elapsed.
    """

    def __init__(self, start_rssi: float, segments: list[ScriptSegment]) -> None:
        self._start_rssi = start_rssi
        self._segments = segments

    def value_at(self, elapsed_seconds: float) -> float:
        previous_rssi = self._start_rssi
        segment_start = 0.0
        for segment in self._segments:
            segment_end = segment_start + segment.duration_seconds
            if elapsed_seconds < segment_end or segment.duration_seconds == 0:
                if segment.duration_seconds == 0:
                    return segment.target_rssi
                fraction = (elapsed_seconds - segment_start) / segment.duration_seconds
                return previous_rssi + fraction * (segment.target_rssi - previous_rssi)
            previous_rssi = segment.target_rssi
            segment_start = segment_end
        return self._segments[-1].target_rssi


class SimulatedScanner:
    """Synthesizes RSSI readings and serves them over the same WS contract as bridge.py."""

    def __init__(
        self,
        scanner_id: str,
        target_name: str,
        host: str,
        port: int,
        base_rssi: float,
        noise_db: float,
        script: list[ScriptSegment] | None,
        lost_after_seconds: float | None,
        rng: random.Random,
    ) -> None:
        self.scanner_id = scanner_id
        self.target_name = target_name
        self.host = host
        self.port = port
        self.noise_db = noise_db
        self.lost_after_seconds = lost_after_seconds
        self._rng = rng

        self._rssi_source = ScriptedRssiSource(base_rssi, script) if script else None
        self._static_base_rssi = base_rssi

        self.smoothed_rssi: float | None = None
        self.raw_rssi: float | None = None
        self.is_lost = False
        self._start_monotonic: float | None = None
        self._last_seen_monotonic: float | None = None

        self.clients: set = set()
        self._stop_event = asyncio.Event()

    def _current_base_rssi(self, elapsed_seconds: float) -> float:
        if self._rssi_source is not None:
            return self._rssi_source.value_at(elapsed_seconds)
        return self._static_base_rssi

    def _tick(self, elapsed_seconds: float) -> None:
        if self.lost_after_seconds is not None and elapsed_seconds >= self.lost_after_seconds:
            self.is_lost = True
            return

        base = self._current_base_rssi(elapsed_seconds)
        noise = self._rng.uniform(-self.noise_db, self.noise_db)
        self.raw_rssi = base + noise
        self.smoothed_rssi = apply_ema(self.smoothed_rssi, self.raw_rssi)
        self.is_lost = False
        loop = asyncio.get_running_loop()
        self._last_seen_monotonic = loop.time()

    def _current_message(self) -> dict:
        if self.is_lost or self._last_seen_monotonic is None:
            return build_lost_message(self.scanner_id, self.target_name)
        loop = asyncio.get_running_loop()
        last_seen_ms = int(max(0.0, (loop.time() - self._last_seen_monotonic) * 1000))
        return build_reading_message(
            self.scanner_id, self.target_name, self.raw_rssi, self.smoothed_rssi, last_seen_ms
        )

    async def _handle_client(self, websocket) -> None:
        self.clients.add(websocket)
        try:
            await websocket.send(json.dumps(self._current_message()))
            async for _ in websocket:
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)

    async def _simulation_loop(self) -> None:
        loop = asyncio.get_running_loop()
        self._start_monotonic = loop.time()
        while not self._stop_event.is_set():
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            elapsed = loop.time() - self._start_monotonic
            self._tick(elapsed)

    async def _broadcast_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
            message = self._current_message()
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

    async def run(self) -> None:
        server = await websockets.serve(self._handle_client, self.host, self.port)
        print(f"[sim:{self.scanner_id}] WebSocket server listening on ws://{self.host}:{self.port}")
        print(f"[sim:{self.scanner_id}] Simulating {self.target_name!r}, base={self._static_base_rssi:.1f}dBm.")
        print(f"[sim:{self.scanner_id}] Press Ctrl+C to stop.\n")

        sim_task = asyncio.ensure_future(self._simulation_loop())
        broadcast_task = asyncio.ensure_future(self._broadcast_loop())

        try:
            await self._stop_event.wait()
        finally:
            sim_task.cancel()
            broadcast_task.cancel()
            for task in (sim_task, broadcast_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            for client in list(self.clients):
                await client.close()
            server.close()
            await server.wait_closed()

    def stop(self) -> None:
        self._stop_event.set()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aether Protocol headless simulated BLE scanner.")
    parser.add_argument("--scanner", default=DEFAULT_SCANNER_ID, help=f"Identifier for this simulated scanner (default: {DEFAULT_SCANNER_ID!r}).")
    parser.add_argument("--name", default=DEFAULT_TARGET_NAME, help=f"Simulated target BLE local name (default: {DEFAULT_TARGET_NAME!r}).")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"WebSocket bind host (default: {DEFAULT_HOST}).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"WebSocket bind port (default: {DEFAULT_PORT}).")
    parser.add_argument("--base-rssi", type=float, default=DEFAULT_BASE_RSSI, help=f"Baseline simulated RSSI in dBm (default: {DEFAULT_BASE_RSSI:.1f}).")
    parser.add_argument("--noise", type=float, default=DEFAULT_NOISE_DB, help=f"Uniform +/- noise in dB applied to each sample (default: {DEFAULT_NOISE_DB:.1f}).")
    parser.add_argument(
        "--script",
        default=None,
        help="Optional ramp script: 'rssi@seconds,rssi@seconds,...' - linearly ramps base RSSI "
        "through each segment in order, then holds the final value.",
    )
    parser.add_argument("--lost-after", type=float, default=None, help="Go silent/emit 'lost' after this many seconds.")
    parser.add_argument("--seed", type=int, default=None, help="Seed the RNG for reproducible noise.")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    script = parse_script(args.script) if args.script else None
    rng = random.Random(args.seed)
    scanner = SimulatedScanner(
        scanner_id=args.scanner,
        target_name=args.name,
        host=args.host,
        port=args.port,
        base_rssi=args.base_rssi,
        noise_db=args.noise,
        script=script,
        lost_after_seconds=args.lost_after,
        rng=rng,
    )

    try:
        await scanner.run()
    except KeyboardInterrupt:
        scanner.stop()


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[sim] Stopped by user.")


if __name__ == "__main__":
    main()
