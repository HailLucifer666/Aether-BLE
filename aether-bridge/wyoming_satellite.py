"""Aether Protocol Phase 8 - minimal Wyoming-protocol satellite for Home Assistant.

Exposes Aether as a wake+TTS satellite that a real Home Assistant instance
can add to its Assist pipeline. Uses the official `wyoming` pip package for
the JSONL-header + binary-payload framing (AsyncServer/AsyncEventHandler) -
this module does not hand-roll any wire parsing.

Architecture (per docs/phase8/ARCHITECTURE.md):
    Home Assistant Assist pipeline
      -> Wyoming TCP connection to this server
      -> "detect" event registers the satellite's wake capability
      -> on Aether-side wake acceptance (owner match), emit "detection"
      -> HA's own STT/intent/LLM pipeline runs (this module does NOT
         duplicate that - its job is purely "am I the arbitrated owner
         right now")
      -> HA sends "synthesize" with response text
      -> this module calls the SAME Piper TTS path aggregator.py uses
         (piper_tts.py's synthesize_speech) and streams audio back as
         AudioStart/AudioChunk/AudioStop events

This module connects OUT to the aggregator's existing WebSocket endpoint as
a plain client (same pattern as bridge.py/wake_listener.py) purely to learn
the current owner and to know when a wake was accepted for its own node id -
it does not import aggregator.py and the aggregator does not import this
module.
"""

import argparse
import asyncio
import json
import sys
import time

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import (
    Attribution,
    Info,
    Satellite,
    TtsProgram,
    TtsVoice,
    WakeModel,
    WakeProgram,
)
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize
from wyoming.wake import Detect, Detection

import websockets
from piper_tts import PIPER_SAMPLE_RATE, synthesize_speech

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 10700
DEFAULT_AGGREGATOR_URL = "ws://127.0.0.1:8766"
DEFAULT_NODE_ID = "aether-satellite"
DEFAULT_WAKEWORD_NAME = "hey_jarvis"

AUDIO_CHUNK_BYTES = 2048  # streamed chunk size for synthesize playback

# Security: this server binds to all interfaces by default (HA usually runs
# on a different LAN host, so localhost-only isn't viable) with zero
# handshake per the project's existing LAN-trust model - but unlike the
# aggregator's plaintext WS traffic, this is a NEW listening service, so any
# LAN peer could otherwise force unbounded/rapid TTS synthesis. These two
# caps bound that to a nuisance rather than a real resource-exhaustion DoS.
MAX_SYNTHESIZE_TEXT_LENGTH = 2000
MIN_SYNTHESIZE_INTERVAL_SECONDS = 0.5


def build_info(node_id: str, wakeword_name: str) -> Info:
    """Build the Wyoming `Info` response advertising this satellite's
    wake + TTS capabilities, per docs/phase8/ARCHITECTURE.md's minimal event
    set. This is what a real Home Assistant instance uses to register Aether
    as an Assist satellite - full HA-side registration is still
    manual-verification (needs a real HA instance)."""
    attribution = Attribution(name="aether-protocol", url="https://github.com/")
    return Info(
        satellite=Satellite(
            name=node_id,
            attribution=attribution,
            installed=True,
            description="Aether Protocol arbitrated wake+TTS satellite",
            version="0.8.0",
            area=None,
            has_vad=False,
            active_wake_words=[wakeword_name],
            max_active_wake_words=1,
            supports_trigger=False,
        ),
        wake=[
            WakeProgram(
                name="openwakeword",
                attribution=attribution,
                installed=True,
                description="openWakeWord via Aether's wake_listener.py",
                version=None,
                models=[
                    WakeModel(
                        name=wakeword_name,
                        attribution=attribution,
                        installed=True,
                        description="Pretrained openWakeWord model",
                        version=None,
                        languages=["en"],
                        phrase=wakeword_name.replace("_", " "),
                    )
                ],
            )
        ],
        tts=[
            TtsProgram(
                name="piper",
                attribution=attribution,
                installed=True,
                description="Piper neural TTS via Aether's piper_tts.py",
                version=None,
                voices=[
                    TtsVoice(
                        name="en_US-lessac-medium",
                        attribution=attribution,
                        installed=True,
                        description="Piper en_US lessac voice",
                        version=None,
                        languages=["en_US"],
                        speakers=None,
                    )
                ],
                supports_synthesize_streaming=False,
            )
        ],
    )


class AetherOwnershipTracker:
    """Tracks the aggregator's currently-elected owner + node-id membership
    by connecting to the aggregator's WS endpoint as a plain client (same
    role as any dashboard consumer). This is the seam that ties Wyoming's
    "detect"/"detection" events to Aether's real arbitration logic: a
    "detect" from HA is only turned into a "detection" once this satellite's
    own node_id matches the aggregator's owner - i.e. once Aether's OWN wake
    acceptance logic (aggregator.py:trigger_wake / lines 588-602, untouched)
    has decided this node is the one that should respond.
    """

    def __init__(self, aggregator_url: str, node_id: str) -> None:
        self.aggregator_url = aggregator_url
        self.node_id = node_id
        self._owner: str | None = None
        self._stop_event = asyncio.Event()

    def is_owner(self) -> bool:
        return self._owner == self.node_id

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(self.aggregator_url) as ws:
                    async for raw in ws:
                        try:
                            message = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if isinstance(message, dict) and message.get("type") == "election":
                            self._owner = message.get("owner")
            except (OSError, websockets.exceptions.WebSocketException):
                pass
            if self._stop_event.is_set():
                break
            await asyncio.sleep(2.0)

    def stop(self) -> None:
        self._stop_event.set()


class AetherSatelliteHandler(AsyncEventHandler):
    """Per-connection Wyoming event handler.

    Handles the minimal event set HA needs to register + use this satellite:
        Describe    -> Info (capability advertisement)
        Detect      -> acknowledged; this satellite decides on its own
                       (via `ownership`) whether to emit Detection
        Synthesize  -> generates audio via the shared Piper TTS path and
                       streams it back as AudioStart/AudioChunk/AudioStop
    """

    def __init__(self, reader, writer, node_id: str, wakeword_name: str, ownership: AetherOwnershipTracker) -> None:
        super().__init__(reader, writer)
        self.node_id = node_id
        self.wakeword_name = wakeword_name
        self.ownership = ownership
        self._last_synthesize_monotonic: float | None = None

    async def handle_event(self, event: Event) -> bool:
        if Info.is_type(event.type):
            return True  # we never receive Info; ignore defensively

        if event.type == "describe":
            await self.write_event(build_info(self.node_id, self.wakeword_name).event())
            return True

        if Detect.is_type(event.type):
            # HA is asking this satellite to arm wake detection. Aether's
            # real detection source is wake_listener.py -> aggregator.py's
            # existing trigger_wake() (unchanged); here we simply reply with
            # a Detection event once this node is (or becomes) the
            # arbitrated owner, proving the "detect"/"detection" round trip
            # end to end over the Wyoming wire.
            if self.ownership.is_owner():
                await self.write_event(Detection(name=self.wakeword_name).event())
            return True

        if Synthesize.is_type(event.type):
            synthesize = Synthesize.from_event(event)
            await self._handle_synthesize(synthesize.text)
            return True

        return True

    async def _handle_synthesize(self, text: str) -> None:
        """Generate audio via the shared Piper TTS path and stream it back
        as the minimal Wyoming audio event sequence HA expects.

        Rate-limited and length-capped: this server has no auth handshake,
        so without these guards any LAN peer could force unbounded/rapid
        Piper synthesis (a real CPU/audio-nuisance DoS vector, not just
        theoretical - see docs/phase8 security review). A request that
        arrives too soon after the previous one is silently dropped rather
        than queued, matching the project's "degrade, never crash" style.
        """
        now = time.monotonic()
        if (
            self._last_synthesize_monotonic is not None
            and now - self._last_synthesize_monotonic < MIN_SYNTHESIZE_INTERVAL_SECONDS
        ):
            return
        self._last_synthesize_monotonic = now

        text = text[:MAX_SYNTHESIZE_TEXT_LENGTH]
        audio_bytes, sample_rate = await asyncio.to_thread(synthesize_speech, text)
        if audio_bytes is None:
            # Piper failed; degrade gracefully by sending an empty audio
            # stream rather than crashing the connection - mirrors
            # aggregator.py's synthetic-fallback resilience (never let a
            # TTS failure take down the pipeline).
            audio_bytes = b""
            sample_rate = sample_rate or PIPER_SAMPLE_RATE

        await self.write_event(
            AudioStart(rate=sample_rate, width=2, channels=1).event()
        )
        for offset in range(0, len(audio_bytes), AUDIO_CHUNK_BYTES):
            chunk = audio_bytes[offset : offset + AUDIO_CHUNK_BYTES]
            await self.write_event(
                AudioChunk(rate=sample_rate, width=2, channels=1, audio=chunk).event()
            )
        await self.write_event(AudioStop().event())


async def main_async(args: argparse.Namespace) -> None:
    ownership = AetherOwnershipTracker(args.aggregator, args.node_id)
    ownership_task = asyncio.ensure_future(ownership.run())

    def handler_factory(reader, writer):
        return AetherSatelliteHandler(reader, writer, args.node_id, args.wakeword, ownership)

    server = AsyncServer.from_uri(f"tcp://{args.host}:{args.port}")
    print(f"[wyoming_satellite] Listening on tcp://{args.host}:{args.port} as node {args.node_id!r}.")
    print(f"[wyoming_satellite] Tracking aggregator ownership via {args.aggregator}.")
    print("[wyoming_satellite] Press Ctrl+C to stop.\n")

    try:
        await server.run(handler_factory)
    finally:
        ownership.stop()
        ownership_task.cancel()
        try:
            await ownership_task
        except asyncio.CancelledError:
            pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aether Protocol Wyoming wake+TTS satellite for Home Assistant.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"TCP bind host (default: {DEFAULT_HOST}).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP bind port (default: {DEFAULT_PORT}).")
    parser.add_argument(
        "--aggregator", default=DEFAULT_AGGREGATOR_URL,
        help=f"Aggregator WebSocket URL to track ownership from (default: {DEFAULT_AGGREGATOR_URL}).",
    )
    parser.add_argument("--node-id", default=DEFAULT_NODE_ID, help=f"This satellite's Aether scanner id (default: {DEFAULT_NODE_ID!r}).")
    parser.add_argument("--wakeword", default=DEFAULT_WAKEWORD_NAME, help=f"Advertised wake word name (default: {DEFAULT_WAKEWORD_NAME!r}).")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[wyoming_satellite] Stopped by user.")


if __name__ == "__main__":
    main()
