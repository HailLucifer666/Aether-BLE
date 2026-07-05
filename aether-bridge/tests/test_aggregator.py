"""End-to-end tests for the mesh aggregator I/O layer.

Drives a real Aggregator against in-process mock peer WebSocket servers (no
BLE hardware, no bleak) to verify peer-message parsing, EMA re-smoothing,
presence timeout, the election-envelope broadcast, one-shot wake resolution,
and the per-peer calibration_offset wiring. Complements tests/test_election.py,
which covers the pure election logic in isolation.

Each test is a thin sync wrapper around an async helper run via asyncio.run,
so this file needs no pytest-asyncio plugin.

Run with: pytest tests/test_aggregator.py -v
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aggregator as agg_mod
from aggregator import Aggregator
from messages import build_lost_message, build_reading_message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reading(scanner: str, rssi: float) -> dict:
    """A minimal-but-valid reading message, reusing the locked builder."""
    return build_reading_message(scanner, "Beacon", rssi, rssi, 0)


async def _start_peer(scanner_id: str, initial_rssi: float = -60.0):
    """Start a mock peer WS server on an ephemeral port.

    Returns (url, push, server) where push(msg) broadcasts a dict (or raw
    JSON string) to every aggregator currently connected to this peer.

    Like bridge.py / simulated_scanner.py, sends an immediate snapshot
    (a reading) on each new client connect - this is what lets the
    aggregator learn the peer's scanner id without an explicit handshake.
    """
    initial_message = _reading(scanner_id, initial_rssi)
    clients: set = set()

    async def handler(websocket) -> None:
        clients.add(websocket)
        try:
            # Mirror bridge.py:_handle_client: a newly connected client
            # receives the current state immediately, not on next tick.
            await websocket.send(json.dumps(initial_message))
            async for _ in websocket:
                pass  # peer is broadcast-only; inbound is ignored
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            clients.discard(websocket)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}"

    async def push(msg) -> None:
        data = json.dumps(msg) if isinstance(msg, dict) else msg
        for client in list(clients):
            try:
                await client.send(data)
            except websockets.exceptions.ConnectionClosed:
                clients.discard(client)

    return url, push, server


async def _start_aggregator(peer_urls, *, tick_ms=50, offsets=None):
    """Start an Aggregator's server + peer-connection + tick + FSM + broadcast loops.

    Returns (agg, port, stop) where stop() cancels everything and closes the
    server cleanly. The terminal-readout and keypress loops are deliberately
    NOT started - they are presentation/UX only and have no testable effect
    on the election/broadcast state.
    """
    agg = Aggregator(peer_urls, "127.0.0.1", 0, tick_ms, offsets)
    server = await websockets.serve(agg._handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    tasks = [asyncio.ensure_future(agg._peer_connection_loop(u)) for u in peer_urls]
    tasks.append(asyncio.ensure_future(agg._election_tick_loop()))
    tasks.append(asyncio.ensure_future(agg._conversation_fsm_loop()))
    tasks.append(asyncio.ensure_future(agg._broadcast_loop()))

    async def stop():
        agg.stop()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        for client in list(agg.clients):
            await client.close()
        server.close()
        await server.wait_closed()

    return agg, port, stop


async def _drain_peer_connections(agg, expected_count, timeout=2.0):
    """Wait until the aggregator has registered `expected_count` peer ids."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while len(agg._peer_order) < expected_count and loop.time() < deadline:
        await asyncio.sleep(0.02)
    return len(agg._peer_order) >= expected_count


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Peer-message parsing & state application
# ---------------------------------------------------------------------------

def test_reading_message_smooths_and_marks_present():
    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            assert await _drain_peer_connections(agg, 1), "aggregator never connected to peer"
            # The connect-snapshot already pushed -60.0; reinforce and verify.
            await push(_reading("SIM-A", -60.0))
            await asyncio.sleep(0.15)  # let _apply_peer_message run

            state = agg._scanners.get("SIM-A")
            assert state is not None
            assert state.raw_rssi == -60.0
            # First EMA sample returns the raw value unchanged.
            assert state.smoothed_rssi == -60.0
            assert agg._is_present(state) is True
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


def test_lost_message_clears_presence():
    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await push(_reading("SIM-A", -60.0))
            await asyncio.sleep(0.15)
            assert agg._is_present(agg._scanners["SIM-A"]) is True

            await push(build_lost_message("SIM-A", "Beacon"))
            await asyncio.sleep(0.15)
            state = agg._scanners["SIM-A"]
            assert state.raw_rssi is None
            assert state.smoothed_rssi is None
            assert agg._is_present(state) is False
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


def test_presence_timeout_marks_absent(monkeypatch):
    """A scanner that goes silent past PRESENCE_TIMEOUT_SECONDS flips to absent
    even without an explicit 'lost' message."""

    async def inner():
        monkeypatch.setattr(agg_mod, "PRESENCE_TIMEOUT_SECONDS", 0.2)
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await push(_reading("SIM-A", -60.0))
            await asyncio.sleep(0.1)
            assert agg._is_present(agg._scanners["SIM-A"]) is True

            # No further messages; wait past the (patched, tiny) presence window.
            await asyncio.sleep(0.4)
            assert agg._is_present(agg._scanners["SIM-A"]) is False
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


# ---------------------------------------------------------------------------
# Election through the aggregator
# ---------------------------------------------------------------------------

def test_two_scanners_elects_strongest_on_first_contact():
    """Once EMA converges, the louder scanner owns. SIM-B (-55) is 15dB louder
    than SIM-A (-70), comfortably above HYSTERESIS_DB, so handoff is not blocked
    regardless of which scanner won the initial -60/-60 first-contact race."""

    async def inner():
        url_a, push_a, srv_a = await _start_peer("SIM-A", -60.0)
        url_b, push_b, srv_b = await _start_peer("SIM-B", -60.0)
        agg, port, stop = await _start_aggregator([url_a, url_b], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 2)
            # Converge EMA to the target steady-state values.
            for _ in range(8):
                await push_a(_reading("SIM-A", -70.0))  # weaker
                await push_b(_reading("SIM-B", -55.0))  # stronger
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.3)  # let election ticks resolve past hysteresis

            assert agg._owner == "SIM-B"
        finally:
            await stop()
            for s in (srv_a, srv_b):
                s.close()
                await s.wait_closed()

    run(inner())


def test_offset_correction_flips_winner():
    """SIM-B's radio over-reports. Without a calibration offset it steals
    ownership from the truly-closer SIM-A; with the bias cancelled by an
    offset, SIM-A wins instead. Mirrors the kill-test in test_election.py but
    driven through the full aggregator stack including the new offset wiring.

    Uses margins >= HYSTERESIS_DB on both sides so that hysteresis never
    blocks the legitimate handoff regardless of which scanner wins the
    first-contact race, and pushes each reading repeatedly so EMA converges
    to the exact target value before the assertion."""

    async def inner():
        # SIM-A truly closer (-65); SIM-B farther but over-reports (-50, looks
        # 15dB louder). With offset=-20 cancelling a 20dB bias, B calibrated =
        # -70 -> 5dB weaker than A (-65), so A must win instead.
        url_a, push_a, srv_a = await _start_peer("SIM-A", -65.0)
        url_b, push_b, srv_b = await _start_peer("SIM-B", -50.0)

        agg_none, port_none, stop_none = await _start_aggregator(
            [url_a, url_b], tick_ms=50, offsets=None
        )
        agg_corr, port_corr, stop_corr = await _start_aggregator(
            [url_a, url_b], tick_ms=50, offsets={url_b: -20.0}
        )

        try:
            await _drain_peer_connections(agg_none, 2)
            await _drain_peer_connections(agg_corr, 2)

            # Push each target value several times so EMA converges to it
            # (initial reading seeds it; these reinforce it to the asymptote).
            for _ in range(8):
                await push_a(_reading("SIM-A", -65.0))
                await push_b(_reading("SIM-B", -50.0))
                await asyncio.sleep(0.02)
            # Let a few election ticks fire so any first-contact race + the
            # 2-tick hysteresis window fully resolves.
            await asyncio.sleep(0.4)

            assert agg_none._owner == "SIM-B", "without offset the louder radio wrongly wins"
            assert agg_corr._owner == "SIM-A", "with offset the truly-closer scanner must win"
        finally:
            await stop_none()
            await stop_corr()
            for s in (srv_a, srv_b):
                s.close()
                await s.wait_closed()

    run(inner())


# ---------------------------------------------------------------------------
# Broadcast envelope
# ---------------------------------------------------------------------------

def test_election_message_envelope_shape():
    """A dashboard client receives the locked ElectionMessage schema on connect."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await push(_reading("SIM-A", -60.0))
            await asyncio.sleep(0.15)

            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                # _handle_client sends the current snapshot immediately.
                raw = await asyncio.wait_for(client.recv(), timeout=2.0)
                msg = json.loads(raw)

                assert msg["type"] == "election"
                assert isinstance(msg["owner"], (str, type(None)))
                assert isinstance(msg["tick"], int)
                assert isinstance(msg["ts"], str)
                assert isinstance(msg["scanners"], list)
                assert len(msg["scanners"]) == 1
                entry = msg["scanners"][0]
                assert set(entry.keys()) == {
                    "id", "rssi", "smoothedRssi", "lastSeenMs", "present"
                }
                assert entry["id"] == "SIM-A"
                assert entry["present"] is True
                assert entry["smoothedRssi"] == -60.0
                # lastHandoff may be None until a handoff occurs; wakeOutcome is
                # None unless a wake was just triggered. Both keys must exist.
                assert "lastHandoff" in msg
                assert "wakeOutcome" in msg
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


def test_wake_outcome_is_oneshot():
    """trigger_wake() attaches wakeOutcome to exactly one broadcast, then it
    is cleared on the next."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await push(_reading("SIM-A", -60.0))
            await asyncio.sleep(0.15)

            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                # Discard the immediate-connect snapshot.
                await asyncio.wait_for(client.recv(), timeout=2.0)

                agg.trigger_wake()

                found_wake = False
                found_clear = False
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while loop.time() < deadline and not found_clear:
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("wakeOutcome") is not None:
                        found_wake = True
                        results = msg["wakeOutcome"]["results"]
                        assert any(r["outcome"] == "ACCEPTED" for r in results)
                    elif found_wake:
                        found_clear = True

                assert found_wake, "wakeOutcome never appeared in a broadcast"
                assert found_clear, "wakeOutcome was not cleared on the following broadcast"
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_malformed_peer_message_is_ignored():
    """Non-JSON and non-dict/missing-type messages must not crash the aggregator
    or mutate any scanner state."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await push(_reading("SIM-A", -60.0))
            await asyncio.sleep(0.15)
            before = agg._scanners["SIM-A"].smoothed_rssi

            await push("not json at all")
            await push("{bad json")
            await push(json.dumps(["not", "a", "dict"]))
            await push(json.dumps({"no_type_field": True}))
            await asyncio.sleep(0.15)

            # State is unchanged: still present, same smoothed value.
            state = agg._scanners["SIM-A"]
            assert agg._is_present(state) is True
            assert state.smoothed_rssi == before
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def test_parse_peer_urls_accepts_bare_urls():
    urls, offsets = agg_mod.parse_peer_urls("ws://127.0.0.1:9001,ws://127.0.0.1:9002")
    assert urls == ["ws://127.0.0.1:9001", "ws://127.0.0.1:9002"]
    assert offsets == {"ws://127.0.0.1:9001": 0.0, "ws://127.0.0.1:9002": 0.0}


def test_parse_peer_urls_accepts_inline_offsets():
    raw = "ws://127.0.0.1:9001,ws://127.0.0.1:9002=-5.0"
    urls, offsets = agg_mod.parse_peer_urls(raw)
    assert urls == ["ws://127.0.0.1:9001", "ws://127.0.0.1:9002"]
    assert offsets == {"ws://127.0.0.1:9001": 0.0, "ws://127.0.0.1:9002": -5.0}


def test_parse_peer_urls_rejects_empty():
    with pytest.raises(ValueError):
        agg_mod.parse_peer_urls("")


def test_parse_peer_urls_rejects_bad_offset():
    with pytest.raises(ValueError):
        agg_mod.parse_peer_urls("ws://127.0.0.1:9001=notanumber")


# ---------------------------------------------------------------------------
# Phase 4: --ranging-geometry CLI flag (the wall demo)
# ---------------------------------------------------------------------------

def test_parse_ranging_geometry_parses_in_and_out():
    geometry = agg_mod.parse_ranging_geometry("A=1.5:in,B=2.5:out")
    assert geometry == {
        "A": (1.5, "in"),
        "B": (2.5, "out"),
    }


def test_parse_ranging_geometry_skips_empty_segments_and_strips():
    geometry = agg_mod.parse_ranging_geometry(" A = 1.5 : in , , B=2.0:out , ")
    assert geometry == {"A": (1.5, "in"), "B": (2.0, "out")}


def test_parse_ranging_geometry_rejects_bad_room():
    with pytest.raises(ValueError):
        agg_mod.parse_ranging_geometry("A=1.5:wall")


def test_parse_ranging_geometry_rejects_bad_distance():
    with pytest.raises(ValueError):
        agg_mod.parse_ranging_geometry("A=close:in")


def test_parse_ranging_geometry_rejects_missing_room():
    with pytest.raises(ValueError):
        agg_mod.parse_ranging_geometry("A=1.5")


def test_ranging_geometry_wall_demo_overrides_ble_with_room_containment():
    """The Phase 4 killer demo: BLE ranks scanner-B as owner, but the geometry
    declares B behind a wall (does not hear the chirp). The geometry-built
    source must drop B from measurements, so fuse() overrides BLE and hands
    ownership to the in-room scanner A with reason chirp-room-containment."""
    from election import ScannerState
    from ranging import Contest, fuse

    def scanner(id_, rssi):
        return ScannerState(id=id_, smoothed_rssi=rssi, present=True)

    scanners = [scanner("A", -62.5), scanner("B", -62.0)]  # B 0.5dB louder
    contest = Contest(
        incumbent_id="A",
        challenger_id="B",
        incumbent_rssi=-62.5,
        challenger_rssi=-62.0,
        at_tick=1,
    )
    # BLE's owner pick is B (the louder, wrong scanner).
    ble_owner = "B"

    source = agg_mod.make_geometry_ranging_source(
        {"A": (1.5, "in"), "B": (2.5, "out")}
    )
    chirp = source(contest, tick=2)
    assert chirp is not None
    # B is behind the wall -> absent from measurements; A is present.
    heard_ids = {m.scanner_id for m in chirp.measurements}
    assert "B" not in heard_ids, "behind-wall scanner must not appear in measurements"
    assert "A" in heard_ids
    assert chirp.winner_id == "A"

    result = fuse(ble_owner, scanners, contest, chirp)
    assert result.owner == "A", "fusion must override BLE's wrong pick with the in-room scanner"
    assert result.reason == "chirp-room-containment"
    assert result.overridden_by_ranging is True


def test_ranging_geometry_default_keeps_both_parties_in_room():
    """Regression guard: with no geometry override, the default source produces
    measurements for both contest parties at the documented distances, so
    fusion stays in the chirp-confirmed / chirp-resolved-tie family (never
    room-containment)."""
    from election import ScannerState
    from ranging import Contest, fuse

    def scanner(id_, rssi):
        return ScannerState(id=id_, smoothed_rssi=rssi, present=True)

    scanners = [scanner("A", -60.0), scanner("B", -60.0)]
    contest = Contest(
        incumbent_id="A", challenger_id="B",
        incumbent_rssi=-60.0, challenger_rssi=-60.0, at_tick=1,
    )
    # Default source (no geometry) - same callable the aggregator uses by default.
    chirp = agg_mod.synthetic_ranging_source(contest, tick=2)
    assert chirp is not None
    heard_ids = {m.scanner_id for m in chirp.measurements}
    assert heard_ids == {"A", "B"}, "default geometry must keep both parties in-room"
    assert chirp.same_room is True

    result = fuse("A", scanners, contest, chirp)
    assert result.reason in {"chirp-confirmed", "chirp-resolved-tie"}, (
        f"default geometry must not produce a room-containment override, got {result.reason!r}"
    )


# ---------------------------------------------------------------------------
# Phase 3: portable conversation state (say, FSM, broadcast envelope)
# ---------------------------------------------------------------------------

def test_say_with_empty_text_is_ignored():
    """An inbound say with empty/whitespace text must not start an utterance."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await asyncio.sleep(0.15)
            assert agg._owner == "SIM-A"

            await agg._handle_say("   ")
            await asyncio.sleep(0.05)
            assert agg._conversation.utterance is None
            assert agg._conversation.transcript == ()
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


def test_say_with_no_owner_is_ignored():
    """A say before any peer has claimed ownership is a no-op."""

    async def inner():
        # Aggregator with no peers connected -> owner is None.
        agg, port, stop = await _start_aggregator([], tick_ms=50)
        try:
            assert agg._owner is None
            await agg._handle_say("hello")
            assert agg._conversation.utterance is None
        finally:
            await stop()

    run(inner())


def test_say_synthetic_fallback_when_edge_tts_missing(monkeypatch):
    """When edge_tts can't be imported, _handle_say falls back to a synthetic
    utterance (no audio, duration estimated from text length) and still
    drives the same conversation FSM. This is the path exercised in every
    CI/test environment where edge_tts isn't installed."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        # Force the lazy edge_tts import inside _generate_speech to fail.
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "edge_tts":
                raise ImportError("simulated: edge_tts not installed")
            return real_import(name, *args, **kwargs)

        # The import happens inside _generate_speech; patch builtins.import
        # used by that function. We use the module's own import context.
        import builtins
        original_import = builtins.__import__
        monkeypatch.setattr(builtins, "__import__", blocking_import)

        try:
            await _drain_peer_connections(agg, 1)
            await asyncio.sleep(0.15)

            await agg._handle_say("hello aether")
            await asyncio.sleep(0.05)

            conv = agg._conversation
            assert conv.utterance is not None
            assert conv.utterance.is_synthetic is True
            assert conv.utterance.audio_base64 is None
            assert conv.utterance.duration_ms > 0
            assert conv.speaking_scanner == "SIM-A"
            assert len(conv.transcript) == 1
            assert conv.transcript[0].text == "hello aether"
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


def test_conversation_message_envelope_shape():
    """A dashboard client receives the conversation message with the locked
    schema after a say, on the same WS as the election broadcast."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await asyncio.sleep(0.15)

            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                # Seed a synthetic utterance directly (bypasses edge_tts/network).
                from conversation import start_utterance
                agg._conversation = start_utterance(
                    agg._conversation,
                    scanner="SIM-A",
                    text="hi",
                    audio_base64=None,
                    duration_ms=1000,
                    is_synthetic=True,
                    tick=agg._tick,
                    ts="12:00:00",
                )
                agg._conversation_dirty = True

                # Drain the immediate-connect election snapshot, then wait
                # for the next broadcast (which now includes a conversation msg).
                saw_conversation = False
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while loop.time() < deadline and not saw_conversation:
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") == "conversation":
                        saw_conversation = True
                        assert "transcript" in msg
                        assert isinstance(msg["transcript"], list)
                        assert msg["utterance"] is not None
                        assert set(msg["utterance"].keys()) == {
                            "text", "audioBase64", "durationMs", "offsetMs", "isSynthetic"
                        }
                        assert msg["utterance"]["isSynthetic"] is True
                        assert msg["speakingScanner"] == "SIM-A"
                        assert msg["phase"] in {"IDLE", "PREPARE", "TRANSFER", "CONFIRM", "RELEASE"}
                        assert "phaseFrom" in msg
                        assert "phaseTo" in msg
                        assert "conversationEvent" in msg

                assert saw_conversation, "never received a conversation broadcast"
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


def test_conversation_message_suppressed_when_empty():
    """Before the first say, the broadcast loop emits NO conversation message -
    only election messages. This keeps the wire quiet until a conversation starts."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await asyncio.sleep(0.15)

            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                saw_types = set()
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 1.0
                while loop.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.2)
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(raw)
                    saw_types.add(msg.get("type"))
                assert "conversation" not in saw_types, (
                    "conversation broadcast emitted before any say was issued"
                )
                assert "election" in saw_types
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


def test_handoff_during_utterance_runs_fsm_through_phases():
    """Seed an utterance owned by SIM-A, then force an ownership change to
    SIM-B by pushing a much stronger reading from SIM-B. Assert the FSM
    transitions through PREPARE -> TRANSFER -> CONFIRM -> RELEASE and ends
    with SIM-B speaking the still-active utterance."""

    async def inner():
        url_a, push_a, srv_a = await _start_peer("SIM-A", -55.0)
        url_b, push_b, srv_b = await _start_peer("SIM-B", -90.0)
        # Use a longer tick so each 200ms phase = exactly 4 ticks.
        agg, port, stop = await _start_aggregator([url_a, url_b], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 2)
            # Make SIM-A the clear owner first.
            for _ in range(8):
                await push_a(_reading("SIM-A", -55.0))
                await push_b(_reading("SIM-B", -90.0))
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.3)
            assert agg._owner == "SIM-A"

            # Seed a long synthetic utterance so it survives the handoff FSM.
            from conversation import start_utterance
            agg._conversation = start_utterance(
                agg._conversation,
                scanner="SIM-A",
                text="the quick brown fox",
                audio_base64=None,
                duration_ms=10_000,
                is_synthetic=True,
                tick=agg._tick,
                ts="12:00:00",
            )

            # Now make SIM-B far stronger - ownership must hand off.
            for _ in range(8):
                await push_a(_reading("SIM-A", -90.0))
                await push_b(_reading("SIM-B", -50.0))
                await asyncio.sleep(0.02)

            # Wait long enough for the election hysteresis (2 ticks) plus the
            # full 4-phase FSM (4 phases * 4 ticks * 50ms = 800ms).
            await asyncio.sleep(2.0)

            conv = agg._conversation
            # FSM must have completed back to IDLE.
            assert conv.phase == "IDLE", f"expected IDLE, got {conv.phase}"
            # Speaking must have migrated to SIM-B.
            assert conv.speaking_scanner == "SIM-B"
            # Utterance still active (duration is 10s, we only waited ~2s).
            assert conv.utterance is not None
        finally:
            await stop()
            for s in (srv_a, srv_b):
                s.close()
                await s.wait_closed()

    run(inner())


def test_conversation_event_is_oneshot():
    """The conversationEvent field is attached to exactly one broadcast, then
    cleared - mirroring wakeOutcome's one-shot semantics. Verified by seeding a
    transcript entry (so conversation broadcasts keep flowing after the event)
    and checking the event appears once then is absent on the next."""

    async def inner():
        url, push, peer_server = await _start_peer("SIM-A")
        agg, port, stop = await _start_aggregator([url], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 1)
            await asyncio.sleep(0.15)

            # Seed a transcript entry so conversation messages keep flowing
            # even after the one-shot event is consumed.
            from conversation import start_utterance, finish_utterance
            agg._conversation = start_utterance(
                agg._conversation,
                scanner="SIM-A",
                text="hi",
                audio_base64=None,
                duration_ms=10_000,
                is_synthetic=True,
                tick=agg._tick,
                ts="12:00:00",
            )
            agg._conversation = finish_utterance(agg._conversation)
            assert agg._conversation.transcript  # transcript retained

            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                # Discard the immediate snapshot.
                await asyncio.wait_for(client.recv(), timeout=1.0)

                # Stage a conversation event directly.
                agg._stage_conversation_event("PREPARE", "SIM-A", "SIM-B")
                agg._conversation_dirty = True

                event_count = 0
                cleared_seen = False
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while loop.time() < deadline and not (event_count >= 1 and cleared_seen):
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") != "conversation":
                        continue
                    if msg.get("conversationEvent") is not None:
                        event_count += 1
                    elif event_count >= 1:
                        cleared_seen = True

                assert event_count == 1, (
                    f"conversationEvent should appear on exactly one broadcast, "
                    f"appeared on {event_count}"
                )
                assert cleared_seen, "never saw a follow-up broadcast without the event"
        finally:
            await stop()
            peer_server.close()
            await peer_server.wait_closed()

    run(inner())


# ---------------------------------------------------------------------------
# Phase 4: tiered ranging (contest detection, chirp fusion, broadcast).
#
# These tests drive the full aggregator stack including the new ranging
# integration in _election_tick_loop. They use a deterministic injected
# ranging source (not the default synthetic one) so the fusion outcome is
# pinned and assertable. The ranging loop itself is started alongside the
# other loops in _start_aggregator_with_ranging below.
# ---------------------------------------------------------------------------

async def _start_aggregator_with_ranging(peer_urls, *, tick_ms=50, offsets=None, ranging_source=None):
    """Like _start_aggregator but also starts the Phase 4 ranging loop.

    The standard _start_aggregator deliberately starts only the election +
    FSM + broadcast loops; the ranging loop is Phase 4-specific so it gets
    its own starter to keep the pre-Phase-4 tests untouched.
    """
    agg = Aggregator(peer_urls, "127.0.0.1", 0, tick_ms, offsets, ranging_source=ranging_source)
    server = await websockets.serve(agg._handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    tasks = [asyncio.ensure_future(agg._peer_connection_loop(u)) for u in peer_urls]
    tasks.append(asyncio.ensure_future(agg._election_tick_loop()))
    tasks.append(asyncio.ensure_future(agg._conversation_fsm_loop()))
    tasks.append(asyncio.ensure_future(agg._ranging_loop()))
    tasks.append(asyncio.ensure_future(agg._broadcast_loop()))

    async def stop():
        agg.stop()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        for client in list(agg.clients):
            await client.close()
        server.close()
        await server.wait_closed()

    return agg, port, stop


def test_no_ranging_broadcast_when_election_is_uncontested():
    """Before any photo-finish, the wire carries only election messages -
    the ranging message is suppressed entirely (mirrors how the conversation
    message is suppressed before the first say)."""

    async def inner():
        # SIM-A is a runaway winner (15 dB louder); no contest ever fires.
        url_a, push_a, srv_a = await _start_peer("SIM-A", -55.0)
        url_b, push_b, srv_b = await _start_peer("SIM-B", -70.0)
        agg, port, stop = await _start_aggregator_with_ranging([url_a, url_b], tick_ms=50)

        try:
            await _drain_peer_connections(agg, 2)
            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                saw_types = set()
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 1.0
                while loop.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.2)
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(raw)
                    saw_types.add(msg.get("type"))
                assert "ranging" not in saw_types, (
                    "ranging broadcast emitted with no contest active"
                )
                assert "election" in saw_types
        finally:
            await stop()
            for s in (srv_a, srv_b):
                s.close()
                await s.wait_closed()

    run(inner())


def test_contest_fires_and_chirp_overrides_ble_owner():
    """Two scanners within the contest margin escalate to tier 2; the injected
    ranging source reports the challenger as closer; the aggregator's owner
    flips to the challenger via fusion (fusion_reason = chirp-resolved-tie)."""

    async def inner():
        # Push both scanners to near-equal calibrated RSSI (within the
        # CONTEST_MARGIN_DB window, below HYSTERESIS_DB) so a contest fires.
        url_a, push_a, srv_a = await _start_peer("SIM-A", -60.0)
        url_b, push_b, srv_b = await _start_peer("SIM-B", -60.0)
        agg, port, stop = await _start_aggregator_with_ranging(
            [url_a, url_b],
            tick_ms=50,
            ranging_source=_make_source({"SIM-A": 2.5, "SIM-B": 1.5}),  # B closer
        )

        try:
            await _drain_peer_connections(agg, 2)
            # Hold both at -60 -> gap 0 -> squarely contested. The lexical
            # tie-break would pick SIM-A as BLE owner forever; the chirp
            # overrides to SIM-B (closer in the injected geometry). After the
            # override SIM-B is also the BLE owner, so subsequent chirps
            # confirm rather than override - both reasons prove tier 2 drove
            # the decision.
            for _ in range(10):
                await push_a(_reading("SIM-A", -60.0))
                await push_b(_reading("SIM-B", -60.0))
                await asyncio.sleep(0.02)
            # Let election ticks + ranging loop settle.
            await asyncio.sleep(1.0)

            assert agg._active_contest is not None, "contest never fired"
            assert agg._last_chirp is not None, "chirp never produced"
            assert agg._last_chirp.winner_id == "SIM-B"
            # The decisive assertion: SIM-B owns, NOT SIM-A (the lexical
            # winner BLE alone would pick). Only a chirp override can get
            # us here given equal RSSI.
            assert agg._owner == "SIM-B", (
                "fusion did not override to chirp winner; without tier 2 the "
                "lexical tie-break would have kept SIM-A as owner forever"
            )
            assert agg._last_fusion_reason in {
                "chirp-resolved-tie", "chirp-confirmed"
            }, f"expected a chirp-driven reason, got {agg._last_fusion_reason!r}"
        finally:
            await stop()
            for s in (srv_a, srv_b):
                s.close()
                await s.wait_closed()

    run(inner())


def test_ranging_event_is_oneshot():
    """The rangingEvent field is attached to exactly one broadcast per chirp,
    then cleared - the same one-shot pattern as wakeOutcome/conversationEvent."""

    async def inner():
        url_a, push_a, srv_a = await _start_peer("SIM-A", -60.0)
        url_b, push_b, srv_b = await _start_peer("SIM-B", -60.0)
        agg, port, stop = await _start_aggregator_with_ranging(
            [url_a, url_b],
            tick_ms=50,
            ranging_source=_make_source({"SIM-A": 1.5, "SIM-B": 2.5}),  # A closer
        )

        try:
            await _drain_peer_connections(agg, 2)
            for _ in range(8):
                await push_a(_reading("SIM-A", -60.0))
                await push_b(_reading("SIM-B", -60.0))
                await asyncio.sleep(0.02)

            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                # Wait for at least one ranging message with a rangingEvent,
                # then confirm a follow-up ranging message arrives WITHOUT it.
                saw_event = False
                saw_clear = False
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while loop.time() < deadline and not (saw_event and saw_clear):
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") != "ranging":
                        continue
                    if msg.get("rangingEvent") is not None:
                        saw_event = True
                    elif saw_event:
                        saw_clear = True
                assert saw_event, "rangingEvent never appeared in a broadcast"
                assert saw_clear, "rangingEvent was never cleared on a follow-up"
        finally:
            await stop()
            for s in (srv_a, srv_b):
                s.close()
                await s.wait_closed()

    run(inner())


def test_ranging_message_envelope_shape():
    """A dashboard client receives the locked RangingMessage schema once a
    contest fires."""

    async def inner():
        url_a, push_a, srv_a = await _start_peer("SIM-A", -60.0)
        url_b, push_b, srv_b = await _start_peer("SIM-B", -60.0)
        agg, port, stop = await _start_aggregator_with_ranging(
            [url_a, url_b],
            tick_ms=50,
            ranging_source=_make_source({"SIM-A": 1.5, "SIM-B": 2.5}),
        )

        try:
            await _drain_peer_connections(agg, 2)
            for _ in range(8):
                await push_a(_reading("SIM-A", -60.0))
                await push_b(_reading("SIM-B", -60.0))
                await asyncio.sleep(0.02)

            async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                saw_ranging = False
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 3.0
                while loop.time() < deadline and not saw_ranging:
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") != "ranging":
                        continue
                    saw_ranging = True
                    assert set(msg.keys()) == {
                        "type", "contest", "chirp", "fusionReason", "rangingEvent"
                    }
                    assert msg["contest"] is not None
                    assert set(msg["contest"].keys()) == {
                        "incumbentId", "challengerId", "incumbentRssi",
                        "challengerRssi", "atTick",
                    }
                    assert msg["chirp"] is not None
                    assert set(msg["chirp"].keys()) == {
                        "measurements", "winnerId", "sameRoom", "resolvedTick",
                    }
                    for m in msg["chirp"]["measurements"]:
                        assert set(m.keys()) == {"scannerId", "tofUs", "distanceM"}
                    assert msg["fusionReason"] in {
                        "ble-only", "chirp-confirmed", "chirp-resolved-tie",
                        "chirp-room-containment",
                    }
                assert saw_ranging, "never received a ranging broadcast"
        finally:
            await stop()
            for s in (srv_a, srv_b):
                s.close()
                await s.wait_closed()

    run(inner())


def _make_source(distances):
    """Build a deterministic ranging source that reads from a fixed distance
    map. Returns the callable expected by Aggregator.__init__."""
    from ranging import chirp_from_measurements, tof_to_distance, ChirpMeasurement

    def source(contest, tick):
        measurements = []
        for scanner_id in (contest.incumbent_id, contest.challenger_id):
            d = distances.get(scanner_id)
            if d is None:
                continue
            measurements.append(
                ChirpMeasurement(
                    scanner_id=scanner_id,
                    tof_us=(d / 343.0) * 1_000_000.0,
                    distance_m=d,
                )
            )
        return chirp_from_measurements(tuple(measurements), contest, tick)

    return source
