"""Integration test for wyoming_satellite.py.

Starts the real AetherSatelliteHandler/AsyncServer on an ephemeral port (no
aggregator connection needed for the describe/synthesize round trip - the
ownership tracker simply stays "not owner" if it can't reach an aggregator,
which is fine for these tests since they don't require Detection), then
drives it with a REAL scripted client built from the wyoming package's own
AsyncClient helper (not a hand-rolled socket parser) - proving the JSONL+
binary framing round-trips for real over an actual TCP connection.

Full Home Assistant-side registration/end-to-end is explicitly NOT verified
here (no real HA instance available to an agent) - see the PRD's
manual-verification-required note. This test only proves the Wyoming wire
protocol itself works against the running server.

Run with: pytest tests/test_wyoming_satellite.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.info import Describe, Info
from wyoming.server import AsyncServer
from wyoming.tts import Synthesize
from wyoming.wake import Detect

import wyoming_satellite as sat_mod


def run(coro):
    return asyncio.run(coro)


async def _start_test_server(node_id="TEST-SAT", is_owner=True):
    """Starts a real wyoming_satellite server on an ephemeral port, with a
    fake ownership tracker (no live aggregator needed for these tests)."""

    class FakeOwnership:
        def is_owner(self) -> bool:
            return is_owner

    ownership = FakeOwnership()

    def handler_factory(reader, writer):
        return sat_mod.AetherSatelliteHandler(reader, writer, node_id, "hey_jarvis", ownership)

    server = AsyncServer.from_uri("tcp://127.0.0.1:0")
    # AsyncTcpServer binds lazily inside run(); we need the port before that,
    # so construct the underlying asyncio server ourselves via start_server
    # and drive AsyncServer's handler_factory callback manually. Simpler:
    # bind directly with asyncio.start_server using the same callback shape
    # AsyncServer.run() uses internally.
    import functools

    async def client_connected(reader, writer):
        await server._handler_callback(handler_factory, reader, writer)

    asyncio_server = await asyncio.start_server(client_connected, "127.0.0.1", 0)
    port = asyncio_server.sockets[0].getsockname()[1]

    async def stop():
        await server.stop()
        asyncio_server.close()
        await asyncio_server.wait_closed()

    return port, stop


def test_describe_returns_info_with_wake_and_tts():
    """A real scripted client sends Describe and receives a real Info event
    back, advertising both wake (openwakeword) and tts (piper) capability -
    the minimal event set Home Assistant needs to register the satellite."""

    async def inner():
        port, stop = await _start_test_server()
        try:
            client = AsyncClient.from_uri(f"tcp://127.0.0.1:{port}")
            await client.connect()
            try:
                await client.write_event(Describe().event())
                event = await asyncio.wait_for(client.read_event(), timeout=3.0)
                assert event is not None
                assert Info.is_type(event.type)
                info = Info.from_event(event)
                assert info.satellite is not None
                assert info.satellite.name == "TEST-SAT"
                assert len(info.wake) == 1
                assert info.wake[0].models[0].name == "hey_jarvis"
                assert len(info.tts) == 1
                assert info.tts[0].name == "piper"
            finally:
                await client.disconnect()
        finally:
            await stop()

    run(inner())


def test_detect_yields_detection_when_owner():
    """A real Detect event, against a server whose (fake) ownership tracker
    reports this node as the current owner, yields a real Detection event
    back - proving the detect/detection round trip over the wire."""

    async def inner():
        port, stop = await _start_test_server(is_owner=True)
        try:
            client = AsyncClient.from_uri(f"tcp://127.0.0.1:{port}")
            await client.connect()
            try:
                await client.write_event(Detect().event())
                event = await asyncio.wait_for(client.read_event(), timeout=3.0)
                assert event is not None
                assert event.type == "detection"
            finally:
                await client.disconnect()
        finally:
            await stop()

    run(inner())


def test_detect_yields_nothing_when_not_owner():
    """When this node is NOT the arbitrated owner, Detect must not produce a
    Detection event - proving Aether's arbitration (not just any wake) gates
    the Wyoming-side detection."""

    async def inner():
        port, stop = await _start_test_server(is_owner=False)
        try:
            client = AsyncClient.from_uri(f"tcp://127.0.0.1:{port}")
            await client.connect()
            try:
                await client.write_event(Detect().event())
                # No Detection should arrive; confirm by timing out rather
                # than receiving one.
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(client.read_event(), timeout=0.5)
            finally:
                await client.disconnect()
        finally:
            await stop()

    run(inner())


def test_synthesize_streams_real_piper_audio():
    """A real Synthesize event drives the shared Piper TTS path and streams
    back a real AudioStart/AudioChunk.../AudioStop sequence with non-empty
    audio bytes - the same synthesis path aggregator.py's _generate_speech
    uses (piper_tts.py)."""

    async def inner():
        port, stop = await _start_test_server()
        try:
            client = AsyncClient.from_uri(f"tcp://127.0.0.1:{port}")
            await client.connect()
            try:
                await client.write_event(Synthesize(text="Testing the Wyoming satellite.").event())

                start_event = await asyncio.wait_for(client.read_event(), timeout=10.0)
                assert start_event is not None
                assert AudioStart.is_type(start_event.type)
                audio_start = AudioStart.from_event(start_event)
                assert audio_start.rate > 0

                total_bytes = 0
                chunk_count = 0
                while True:
                    event = await asyncio.wait_for(client.read_event(), timeout=5.0)
                    assert event is not None
                    if AudioStop.is_type(event.type):
                        break
                    assert AudioChunk.is_type(event.type)
                    chunk = AudioChunk.from_event(event)
                    total_bytes += len(chunk.audio)
                    chunk_count += 1

                assert chunk_count > 0, "no audio chunks streamed"
                assert total_bytes > 0, "streamed audio was empty"
            finally:
                await client.disconnect()
        finally:
            await stop()

    run(inner())
