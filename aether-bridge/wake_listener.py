"""Aether Protocol Phase 8 - real wake-word detection, per-node process.

Mirrors bridge.py's process pattern: this is a lightweight WS CLIENT that
connects OUT to the aggregator's existing WebSocket endpoint and sends the
exact same wire message aggregator.py already handles
(`{"type": "wake"}` - see aggregator.py:769's dispatch and trigger_wake(),
lines 588-602). No wire-protocol change; this module never imports
aggregator.py and the aggregator never imports this module or any audio
library, keeping the aggregator's dependency surface free of mic/ML code.

Pipeline: sounddevice captures live 16kHz mono int16 mic frames ->
openWakeWord's Model.predict() scores each frame against the loaded
wakeword model(s) -> a score crossing THRESHOLD, subject to the debounce
window below, sends the wake message.

The debounce logic (`should_send_wake`) is a small pure function with zero
mic/model/websocket dependencies, specifically so it is unit-testable in
isolation (see tests/test_wake_listener.py) - this mirrors election.py's and
conversation.py's discipline of keeping decision logic free of I/O.
"""

import argparse
import asyncio
import json
import sys

import numpy as np
import websockets

DEFAULT_AGGREGATOR_URL = "ws://127.0.0.1:8766"
DEFAULT_WAKEWORD_MODEL = "hey_jarvis"
DEFAULT_THRESHOLD = 0.5
DEFAULT_SAMPLE_RATE = 16000
# openWakeWord's pretrained models expect 80ms chunks at 16kHz (1280 samples).
CHUNK_SAMPLES = 1280

# Minimum wall-clock seconds between two wake sends. A single spoken wake
# word produces many consecutive frames above threshold (not just one), and
# in the real deployment multiple nearby mics may each detect it once; this
# debounce prevents one utterance from generating a burst of wake events
# from the SAME listener. It does NOT suppress the (correct, by design)
# case of multiple DIFFERENT scanners each sending their own single wake
# event for the same spoken word - that's exactly the scenario
# aggregator.trigger_wake() is built to arbitrate.
DEFAULT_DEBOUNCE_SECONDS = 1.5

RECONNECT_DELAY_SECONDS = 2.0


def should_send_wake(
    last_sent_monotonic: float | None,
    now_monotonic: float,
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
) -> bool:
    """Pure debounce decision: may a wake be sent right now?

    Returns True if no wake has ever been sent (`last_sent_monotonic` is
    None) or if at least `debounce_seconds` have elapsed since the last one.
    Takes plain floats (monotonic-clock-style timestamps) so it has zero
    mic/model/websocket dependencies and is trivially unit-testable with
    fake timestamps - see tests/test_wake_listener.py.
    """
    if last_sent_monotonic is None:
        return True
    return (now_monotonic - last_sent_monotonic) >= debounce_seconds


class WakeListener:
    """Live-audio wake-word loop: mic -> openWakeWord -> debounced WS wake send."""

    def __init__(
        self,
        aggregator_url: str,
        wakeword_model: str = DEFAULT_WAKEWORD_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        self.aggregator_url = aggregator_url
        self.wakeword_model = wakeword_model
        self.threshold = threshold
        self.debounce_seconds = debounce_seconds
        self.sample_rate = sample_rate

        self._last_sent_monotonic: float | None = None
        self._stop_event = asyncio.Event()
        self._ws: "websockets.WebSocketClientProtocol | None" = None

    # -- Wake-word model (lazy import: keeps this module importable, and its
    # pure debounce function testable, without openwakeword/sounddevice
    # actually installed) -----------------------------------------------

    def _load_model(self):
        from openwakeword.model import Model

        return Model(wakeword_models=[self.wakeword_model], inference_framework="onnx")

    async def _send_wake(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "wake"}))
            print("[wake_listener] WAKE sent to aggregator.")
        except websockets.exceptions.ConnectionClosed:
            print("[wake_listener] WARNING: aggregator connection closed while sending wake.")

    def _on_score(self, score: float) -> None:
        """Called synchronously from the sounddevice audio callback thread
        with the latest wakeword score. Schedules the debounce check + send
        onto the asyncio event loop (audio callbacks run off-loop)."""
        if score < self.threshold:
            return
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(self._maybe_wake)

    def _maybe_wake(self) -> None:
        now = asyncio.get_event_loop().time()
        if not should_send_wake(self._last_sent_monotonic, now, self.debounce_seconds):
            return
        self._last_sent_monotonic = now
        asyncio.ensure_future(self._send_wake())

    async def _mic_loop(self) -> None:
        """Captures live mic audio and feeds it to openWakeWord frame-by-frame."""
        import sounddevice as sd

        model = self._load_model()
        loop = asyncio.get_running_loop()
        audio_queue: asyncio.Queue = asyncio.Queue()

        def audio_callback(indata, frames, time_info, status) -> None:
            # Runs on a separate PortAudio thread; hand the raw frame to the
            # asyncio loop via a thread-safe queue put.
            mono = indata[:, 0].copy()
            loop.call_soon_threadsafe(audio_queue.put_nowait, mono)

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            callback=audio_callback,
        ):
            print(
                f"[wake_listener] Listening for wake word '{self.wakeword_model}' "
                f"(threshold={self.threshold}, debounce={self.debounce_seconds}s). "
                "Press Ctrl+C to stop."
            )
            while not self._stop_event.is_set():
                chunk = await audio_queue.get()
                prediction = model.predict(chunk)
                score = max(prediction.values()) if prediction else 0.0
                self._on_score(float(score))

    async def _connection_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.aggregator_url) as ws:
                    self._ws = ws
                    print(f"[wake_listener] Connected to aggregator at {self.aggregator_url}.")
                    async for _ in ws:
                        pass  # this client is send-only; inbound is ignored
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                print(
                    f"[wake_listener] Aggregator {self.aggregator_url} unreachable "
                    f"({exc}); retrying every {RECONNECT_DELAY_SECONDS:.0f}s."
                )
            self._ws = None
            if self._stop_event.is_set():
                break
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def run(self) -> None:
        connection_task = asyncio.ensure_future(self._connection_loop())
        mic_task = asyncio.ensure_future(self._mic_loop())
        try:
            await self._stop_event.wait()
        finally:
            connection_task.cancel()
            mic_task.cancel()
            for task in (connection_task, mic_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def stop(self) -> None:
        self._stop_event.set()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aether Protocol real wake-word listener (openWakeWord -> aggregator wake)."
    )
    parser.add_argument(
        "--aggregator",
        default=DEFAULT_AGGREGATOR_URL,
        help=f"Aggregator WebSocket URL to send wake events to (default: {DEFAULT_AGGREGATOR_URL}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_WAKEWORD_MODEL,
        help=f"openWakeWord pretrained model name (default: {DEFAULT_WAKEWORD_MODEL!r}).",
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"Detection score threshold, 0.0-1.0 (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--debounce-seconds", type=float, default=DEFAULT_DEBOUNCE_SECONDS,
        help=f"Minimum seconds between wake sends from this listener (default: {DEFAULT_DEBOUNCE_SECONDS}).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    listener = WakeListener(
        args.aggregator,
        wakeword_model=args.model,
        threshold=args.threshold,
        debounce_seconds=args.debounce_seconds,
    )
    try:
        asyncio.run(listener.run())
    except KeyboardInterrupt:
        listener.stop()
        print("\n[wake_listener] Stopped by user.")


if __name__ == "__main__":
    main()
