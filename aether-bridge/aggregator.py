"""Aether Protocol Phase 2/3 - multi-scanner mesh aggregator with leader election
and portable conversation state.

Connects OUT as a WebSocket client to each configured peer scanner (real
bridge.py instances or simulated_scanner.py instances - this module never
imports bleak and has no notion of BLE hardware), maintains the latest
reading per scanner, runs election.py on a fixed tick, and serves its own
WebSocket endpoint broadcasting the election state for a dashboard to
consume. Also accepts a "wake" trigger (inbound WS message or a terminal
keypress) that resolves which scanner should react to a wake event given
current ownership.

Phase 3 adds: a "say" inbound message that generates a real TTS utterance
(edge-tts, free/keyless) and assigns it to the current owner; and a
four-phase handoff contract (PREPARE -> TRANSFER -> CONFIRM -> RELEASE)
that migrates the active utterance to the new owner when ownership changes
mid-sentence, so the assistant literally finishes its sentence on the next
device. edge-tts is optional - if it's missing or the network is down, a
synthetic utterance (no audio) keeps the migration demo working.
"""

import argparse
import asyncio
import base64
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime

import websockets

from conversation import (
    PHASE_IDLE,
    SYNTHETIC_MS_PER_CHAR,
    ConversationState,
    advance_phase,
    begin_handoff,
    finish_utterance,
    has_active_utterance,
    start_utterance,
    utterance_progress,
)
from election import ChallengerState, Handoff, ScannerState, elect
from messages import build_conversation_message, build_election_message
from smoothing import apply_ema

DEFAULT_PORT = 8766
DEFAULT_TICK_MS = 400
PEER_RECONNECT_DELAY_SECONDS = 2.0
PRESENCE_TIMEOUT_SECONDS = 6.0
KEYPRESS_POLL_INTERVAL_SECONDS = 0.05
WAKE_KEYS = {b" ", b"\r"}


@dataclass
class PeerScannerState:
    """Latest known state for one peer scanner, as tracked by the aggregator."""

    id: str
    raw_rssi: float | None = None
    smoothed_rssi: float | None = None
    last_seen_monotonic: float | None = None
    last_seen_ms_from_peer: int | None = None


@dataclass
class LastHandoff:
    """Most-recent handoff this aggregator process has observed, for broadcast."""

    from_id: str | None
    to_id: str
    at_tick: int
    ts: str


@dataclass
class WakeOutcome:
    """One-shot wake resolution, attached to exactly the next broadcast."""

    requested_at_tick: int
    ts: str
    owner: str | None
    results: list[dict] = field(default_factory=list)


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Aggregator:
    def __init__(
        self,
        peer_urls: list[str],
        host: str,
        port: int,
        tick_ms: int,
        peer_offsets: dict[str, float] | None = None,
    ) -> None:
        self.peer_urls = peer_urls
        self.host = host
        self.port = port
        self.tick_interval_seconds = tick_ms / 1000.0
        # Per-scanner calibration offsets keyed by peer URL (the only
        # stable identifier available at startup; scanner ids are learned
        # lazily on first message). Applied additively during election -
        # see election.ScannerState.calibrated_rssi.
        self._peer_offsets = peer_offsets or {}

        # Populated lazily as each peer's first message arrives; order of
        # first-contact defines the stable "peers-list order" used in
        # broadcasts, since scanner ids aren't known until then.
        self._peer_order: list[str] = []
        self._scanners: dict[str, PeerScannerState] = {}
        self._id_to_url: dict[str, str] = {}

        self._owner: str | None = None
        self._challenger = ChallengerState()
        self._tick = 0
        self._last_handoff: LastHandoff | None = None
        self._pending_wake_outcome: WakeOutcome | None = None

        # Phase 3: portable conversation state + the one-shot phase-event
        # staging slot (mirrors the wakeOutcome one-shot pattern). The FSM
        # is driven by _conversation_fsm_loop; handoff detection happens in
        # _election_tick_loop. _conversation_dirty forces a broadcast even
        # on ticks where no election change occurred (e.g. mid-handoff).
        self._conversation: ConversationState = ConversationState.empty()
        self._pending_conversation_event: dict | None = None
        self._conversation_dirty = False

        self.clients: set = set()
        self._stop_event = asyncio.Event()

    # -- Peer connections (outbound WS client) ----------------------------

    async def _peer_connection_loop(self, url: str) -> None:
        claimed_id: str | None = None
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(url) as ws:
                    async for raw in ws:
                        message = self._parse_peer_message(raw, url)
                        if message is None:
                            continue
                        scanner_id = message.get("scanner")
                        if scanner_id is None:
                            continue
                        if claimed_id is None:
                            claimed_id = scanner_id
                            self._register_peer_id(scanner_id, claimed_by_url=url)
                        elif claimed_id != scanner_id:
                            print(
                                f"[aggregator] WARNING: peer {url} changed claimed scanner id "
                                f"from {claimed_id!r} to {scanner_id!r} mid-connection; ignoring."
                            )
                            continue
                        self._apply_peer_message(scanner_id, message)
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                print(f"[aggregator] Peer {url} unreachable ({exc}); retrying every {PEER_RECONNECT_DELAY_SECONDS:.0f}s.")
            if self._stop_event.is_set():
                break
            await asyncio.sleep(PEER_RECONNECT_DELAY_SECONDS)

    def _parse_peer_message(self, raw: str, url: str) -> dict | None:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            print(f"[aggregator] WARNING: malformed message from peer {url}; ignoring.")
            return None
        if not isinstance(message, dict) or "type" not in message:
            print(f"[aggregator] WARNING: unexpected message shape from peer {url}; ignoring.")
            return None
        return message

    def _register_peer_id(self, scanner_id: str, claimed_by_url: str) -> None:
        conflicting_url = self._id_to_url.get(scanner_id)
        if conflicting_url is not None and conflicting_url != claimed_by_url:
            print(
                f"[aggregator] WARNING: scanner id {scanner_id!r} claimed by both "
                f"{conflicting_url} and {claimed_by_url} - readings will be mixed."
            )
        self._id_to_url[scanner_id] = claimed_by_url

        if scanner_id not in self._scanners:
            self._scanners[scanner_id] = PeerScannerState(id=scanner_id)
            self._peer_order.append(scanner_id)

    def _apply_peer_message(self, scanner_id: str, message: dict) -> None:
        state = self._scanners[scanner_id]
        loop = asyncio.get_running_loop()
        if message.get("type") == "reading":
            raw_rssi = message.get("rssi")
            if not isinstance(raw_rssi, (int, float)):
                return
            state.raw_rssi = float(raw_rssi)
            state.smoothed_rssi = apply_ema(state.smoothed_rssi, float(raw_rssi))
            state.last_seen_monotonic = loop.time()
        elif message.get("type") == "lost":
            state.last_seen_monotonic = None
            state.raw_rssi = None
            state.smoothed_rssi = None

    def _is_present(self, state: PeerScannerState) -> bool:
        if state.last_seen_monotonic is None:
            return False
        loop = asyncio.get_running_loop()
        return (loop.time() - state.last_seen_monotonic) <= PRESENCE_TIMEOUT_SECONDS

    # -- Election tick ------------------------------------------------------

    def _peer_offset_for(self, scanner_id: str) -> float:
        """Resolve the configured calibration offset for a scanner id.

        Offsets are keyed by peer URL at startup; the id->url mapping is
        populated lazily as peers identify themselves. Returns 0.0 for any
        scanner with no configured offset (the common case).
        """
        url = self._id_to_url.get(scanner_id)
        if url is None:
            return 0.0
        return self._peer_offsets.get(url, 0.0)

    def _election_scanner_states(self) -> list[ScannerState]:
        result = []
        for scanner_id in self._peer_order:
            state = self._scanners[scanner_id]
            present = self._is_present(state)
            result.append(
                ScannerState(
                    id=scanner_id,
                    smoothed_rssi=state.smoothed_rssi if present else None,
                    present=present,
                    calibration_offset=self._peer_offset_for(scanner_id),
                )
            )
        return result

    async def _election_tick_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self.tick_interval_seconds)
            self._tick += 1
            scanners = self._election_scanner_states()
            previous_owner = self._owner
            result = elect(self._owner, scanners, self._challenger)
            self._owner = result.new_owner
            self._challenger = result.challenger
            if result.handoff is not None:
                self._record_handoff(result.handoff)
            # Phase 3: if ownership changed while an utterance is active and
            # being spoken by the outgoing owner, kick off the conversation
            # handoff FSM. Catch every ownership change (not just the
            # Handoff-record path), since first-contact acquisition also
            # changes the owner without emitting a Handoff from elect().
            if (
                previous_owner is not None
                and result.new_owner is not None
                and previous_owner != result.new_owner
            ):
                self._maybe_begin_conversation_handoff(previous_owner, result.new_owner)

    def _maybe_begin_conversation_handoff(self, from_scanner: str, to_scanner: str) -> None:
        if not has_active_utterance(self._conversation):
            return
        before = self._conversation
        after = begin_handoff(before, from_scanner, to_scanner, self._tick)
        if after is before:
            return  # FSM declined (e.g. speaking_scanner != from_scanner)
        self._conversation = after
        self._stage_conversation_event(after.phase, from_scanner, to_scanner)
        self._conversation_dirty = True
        print(
            f"[aggregator] CONVERSATION HANDOFF start tick={self._tick} "
            f"{from_scanner} -> {to_scanner} (phase={after.phase})"
        )

    def _stage_conversation_event(self, phase: str, from_scanner: str | None, to_scanner: str | None) -> None:
        self._pending_conversation_event = {
            "phase": phase,
            "fromScanner": from_scanner,
            "toScanner": to_scanner,
            "atTick": self._tick,
        }

    # -- Conversation FSM ---------------------------------------------------

    async def _conversation_fsm_loop(self) -> None:
        """Drive the four-phase handoff FSM and finish utterances when their
        duration elapses. Runs on the same tick interval as the election loop;
        the actual phase transitions are computed by the pure advance_phase
        function in conversation.py."""
        while not self._stop_event.is_set():
            await asyncio.sleep(self.tick_interval_seconds)
            self._tick  # tick is incremented by the election loop; read-only here
            state = self._conversation

            # Advance the handoff FSM if a phase is in progress.
            if state.phase != PHASE_IDLE:
                previous_phase = state.phase
                previous_speaking = state.speaking_scanner
                advanced = advance_phase(state, self._tick, int(self.tick_interval_seconds * 1000))
                if advanced is not state:
                    self._conversation = advanced
                    self._conversation_dirty = True
                    if advanced.phase != previous_phase:
                        # A real phase transition - stage the one-shot event.
                        # For CONFIRM/RELEASE the "from" is phase_from; the
                        # event marks the new phase the dashboard should act on.
                        self._stage_conversation_event(
                            advanced.phase, advanced.phase_from, advanced.phase_to
                        )
                        if advanced.speaking_scanner != previous_speaking:
                            print(
                                f"[aggregator] CONVERSATION phase={advanced.phase} "
                                f"speaking={advanced.speaking_scanner} tick={self._tick}"
                            )

            # Finish the utterance when its duration has elapsed (only when
            # not mid-handoff, so a migration doesn't get cut short by the
            # wall-clock estimate).
            if state.utterance is not None and state.phase == PHASE_IDLE:
                progress = utterance_progress(state, self._tick, int(self.tick_interval_seconds * 1000))
                if progress >= 1.0:
                    self._conversation = finish_utterance(state)
                    self._conversation_dirty = True
                    print(f"[aggregator] CONVERSATION utterance finished tick={self._tick}")

    def _record_handoff(self, handoff: Handoff) -> None:
        self._last_handoff = LastHandoff(
            from_id=handoff.from_id, to_id=handoff.to_id, at_tick=self._tick, ts=_now_hms()
        )
        print(f"[aggregator] HANDOFF tick={self._tick} {handoff.from_id} -> {handoff.to_id}")

    # -- Wake handling --------------------------------------------------------

    def trigger_wake(self) -> None:
        present_ids = [
            scanner_id for scanner_id in self._peer_order if self._is_present(self._scanners[scanner_id])
        ]
        results = []
        for scanner_id in present_ids:
            outcome = "ACCEPTED" if scanner_id == self._owner else "SUPPRESSED"
            results.append({"id": scanner_id, "outcome": outcome})

        self._pending_wake_outcome = WakeOutcome(
            requested_at_tick=self._tick, ts=_now_hms(), owner=self._owner, results=results
        )
        print(f"[aggregator] WAKE tick={self._tick} owner={self._owner}")
        for entry in results:
            print(f"[aggregator]   {entry['id']}: {entry['outcome']}")

    # -- Conversation: say + TTS generation --------------------------------

    async def _handle_say(self, text: str) -> None:
        """Generate a TTS utterance for `text` and assign it to the current owner.

        Tries edge-tts (free, keyless Microsoft neural voices) first. On ANY
        failure (module missing, network down, rate-limited, malformed text),
        falls back to a synthetic utterance that has no audio but advances
        on the same progress clock - so the handoff-migration demo still
        works offline. Either way the conversation FSM and broadcast are
        identical; only the audio payload differs.
        """
        owner = self._owner
        if owner is None:
            print("[aggregator] SAY ignored - no current owner")
            return

        text = text.strip()
        if not text:
            return

        audio_b64, duration_ms, is_synthetic = await self._generate_speech(text)

        self._conversation = start_utterance(
            self._conversation,
            scanner=owner,
            text=text,
            audio_base64=audio_b64,
            duration_ms=duration_ms,
            is_synthetic=is_synthetic,
            tick=self._tick,
            ts=_now_hms(),
            role="assistant",
        )
        self._conversation_dirty = True
        kind = "synthetic" if is_synthetic else "edge-tts"
        print(
            f"[aggregator] SAY tick={self._tick} owner={owner} "
            f"({kind}, {duration_ms}ms, {len(text)} chars): {text[:60]!r}"
        )

    async def _generate_speech(self, text: str) -> tuple[str | None, int, bool]:
        """Returns (audio_base64_data_url, duration_ms, is_synthetic).

        On any failure returns (None, synthetic_estimate, True).
        """
        try:
            import edge_tts  # imported lazily so the aggregator runs without it
        except Exception as exc:  # noqa: BLE001 - any import failure -> fallback
            print(f"[aggregator] edge-tts unavailable ({exc}); using synthetic utterance.")
            return None, self._synthetic_duration(text), True

        try:
            communicate = edge_tts.Communicate(text, voice="en-US-AriaNeural")
            chunks = []
            total_ms = 0
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    data = chunk.get("data")
                    if isinstance(data, bytes):
                        chunks.append(data)
                elif chunk.get("type") == "WordBoundary":
                    # offset + duration are in 100-nanosecond units; the
                    # last WordBoundary's offset+duration gives total spoken
                    # duration. Fall back to a byte-length estimate below
                    # if no WordBoundary events arrive.
                    offset = chunk.get("offset", 0)
                    duration = chunk.get("duration", 0)
                    total_ms = max(total_ms, int((offset + duration) / 10_000))
            audio_bytes = b"".join(chunks)
            if not audio_bytes:
                raise RuntimeError("edge-tts returned no audio data")
            if total_ms <= 0:
                # ~1 KB mp3 per second of 24kbps neural speech as a rough
                # estimate when WordBoundary metadata is unavailable.
                total_ms = max(self._synthetic_duration(text), int(len(audio_bytes) / 1.0))
            audio_b64 = "data:audio/mp3;base64," + base64.b64encode(audio_bytes).decode("ascii")
            return audio_b64, total_ms, False
        except Exception as exc:  # noqa: BLE001 - network/service failure -> fallback
            print(f"[aggregator] edge-tts generation failed ({exc}); using synthetic utterance.")
            return None, self._synthetic_duration(text), True

    @staticmethod
    def _synthetic_duration(text: str) -> int:
        return max(500, len(text) * SYNTHETIC_MS_PER_CHAR)

    async def _stdin_keypress_loop(self) -> None:
        """Polls for spacebar/enter on Windows via msvcrt; no-op elsewhere."""
        if sys.platform != "win32":
            return
        import msvcrt

        while not self._stop_event.is_set():
            await asyncio.sleep(KEYPRESS_POLL_INTERVAL_SECONDS)
            while msvcrt.kbhit():
                key = msvcrt.getch()
                if key in WAKE_KEYS:
                    self.trigger_wake()

    # -- WS server (broadcast side) ------------------------------------------

    def _current_scanner_entries(self) -> list[dict]:
        entries = []
        for scanner_id in self._peer_order:
            state = self._scanners[scanner_id]
            present = self._is_present(state)
            last_seen_ms = None
            if present and state.last_seen_monotonic is not None:
                loop = asyncio.get_running_loop()
                last_seen_ms = int(max(0.0, (loop.time() - state.last_seen_monotonic) * 1000))
            entries.append(
                {
                    "id": scanner_id,
                    "rssi": state.raw_rssi if present else None,
                    "smoothedRssi": state.smoothed_rssi if present else None,
                    "lastSeenMs": last_seen_ms,
                    "present": present,
                }
            )
        return entries

    def _current_message(self) -> dict:
        last_handoff_dict = None
        if self._last_handoff is not None:
            last_handoff_dict = {
                "from": self._last_handoff.from_id,
                "to": self._last_handoff.to_id,
                "atTick": self._last_handoff.at_tick,
                "ts": self._last_handoff.ts,
            }
        wake_outcome_dict = None
        if self._pending_wake_outcome is not None:
            wake_outcome_dict = {
                "requestedAtTick": self._pending_wake_outcome.requested_at_tick,
                "ts": self._pending_wake_outcome.ts,
                "owner": self._pending_wake_outcome.owner,
                "results": self._pending_wake_outcome.results,
            }
        return build_election_message(
            owner=self._owner,
            tick=self._tick,
            scanners=self._current_scanner_entries(),
            last_handoff=last_handoff_dict,
            wake_outcome=wake_outcome_dict,
        )

    async def _handle_client(self, websocket) -> None:
        self.clients.add(websocket)
        try:
            await websocket.send(json.dumps(self._current_message()))
            async for raw in websocket:
                await self._handle_inbound_client_message(raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)

    async def _handle_inbound_client_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(message, dict):
            return
        msg_type = message.get("type")
        if msg_type == "wake":
            self.trigger_wake()
        elif msg_type == "say":
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                # TTS generation is async; dispatch as a task so the inbound
                # handler returns promptly. The task sets _conversation_dirty
                # when the utterance is ready, which the broadcast loop picks up.
                asyncio.ensure_future(self._handle_say(text))

    async def _broadcast_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self.tick_interval_seconds)
            election_message = self._current_message()
            # wakeOutcome is one-shot: clear it immediately after building
            # this broadcast so it is attached to exactly one message.
            self._pending_wake_outcome = None
            conversation_message = self._current_conversation_message()
            # conversationEvent is one-shot too.
            self._pending_conversation_event = None
            if not self.clients:
                # Even with no clients, clear the dirty flag so it doesn't
                # queue up an unnecessary broadcast on the next connect.
                self._conversation_dirty = False
                continue
            payloads = [json.dumps(election_message)]
            # Only attach the conversation message when there's something to
            # say (active utterance, non-empty transcript, or a pending event).
            # This keeps the wire quiet when no conversation has started.
            if conversation_message is not None:
                payloads.append(json.dumps(conversation_message))
            self._conversation_dirty = False
            stale = []
            for client in self.clients:
                try:
                    for payload in payloads:
                        await client.send(payload)
                except websockets.exceptions.ConnectionClosed:
                    stale.append(client)
            for client in stale:
                self.clients.discard(client)

    def _current_conversation_message(self) -> dict | None:
        """Build the conversation broadcast, or None if there's nothing to send.

        Suppresses the message entirely when the conversation is empty (no
        transcript, no active utterance) AND no one-shot event is pending -
        so before the first "say", the wire carries only election messages.
        """
        state = self._conversation
        if (
            state.utterance is None
            and not state.transcript
            and self._pending_conversation_event is None
        ):
            return None

        transcript_dicts = [
            {
                "id": entry.id,
                "scanner": entry.scanner,
                "role": entry.role,
                "text": entry.text,
                "ts": entry.ts,
            }
            for entry in state.transcript
        ]
        utterance_dict = None
        if state.utterance is not None:
            utterance_dict = {
                "text": state.utterance.text,
                "audioBase64": state.utterance.audio_base64,
                "durationMs": state.utterance.duration_ms,
                "offsetMs": state.utterance.offset_ms,
                "isSynthetic": state.utterance.is_synthetic,
            }
        return build_conversation_message(
            transcript=transcript_dicts,
            utterance=utterance_dict,
            speaking_scanner=state.speaking_scanner,
            phase=state.phase,
            phase_from=state.phase_from,
            phase_to=state.phase_to,
            conversation_event=self._pending_conversation_event,
        )

    # -- Terminal readout ---------------------------------------------------

    def _render_terminal_line(self) -> str:
        parts = [f"tick={self._tick:6d}", f"owner={self._owner or '-':8s}"]
        for scanner_id in self._peer_order:
            state = self._scanners[scanner_id]
            present = self._is_present(state)
            if present and state.smoothed_rssi is not None:
                parts.append(f"{scanner_id}={state.smoothed_rssi:6.1f}")
            else:
                parts.append(f"{scanner_id}={'LOST':>6s}")
        return "\r" + "  ".join(parts) + " " * 10

    async def _terminal_readout_loop(self) -> None:
        while not self._stop_event.is_set():
            sys.stdout.write(self._render_terminal_line())
            sys.stdout.flush()
            await asyncio.sleep(0.2)

    # -- Lifecycle ------------------------------------------------------------

    async def run(self) -> None:
        server = await websockets.serve(self._handle_client, self.host, self.port)
        print(f"[aggregator] WebSocket server listening on ws://{self.host}:{self.port}")
        print(f"[aggregator] Peers: {', '.join(self.peer_urls)}")
        print(f"[aggregator] Tick interval: {self.tick_interval_seconds * 1000:.0f}ms.")
        print("[aggregator] Press Space/Enter to trigger a wake. Press Ctrl+C to stop.\n")

        peer_tasks = [asyncio.ensure_future(self._peer_connection_loop(url)) for url in self.peer_urls]
        tick_task = asyncio.ensure_future(self._election_tick_loop())
        conversation_task = asyncio.ensure_future(self._conversation_fsm_loop())
        broadcast_task = asyncio.ensure_future(self._broadcast_loop())
        readout_task = asyncio.ensure_future(self._terminal_readout_loop())
        keypress_task = asyncio.ensure_future(self._stdin_keypress_loop())

        background_tasks = [
            tick_task,
            conversation_task,
            broadcast_task,
            readout_task,
            keypress_task,
            *peer_tasks,
        ]

        try:
            await self._stop_event.wait()
        finally:
            for task in background_tasks:
                task.cancel()
            for task in background_tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            for client in list(self.clients):
                await client.close()
            server.close()
            await server.wait_closed()
            print()

    def stop(self) -> None:
        self._stop_event.set()


def parse_peer_urls(raw: str) -> tuple[list[str], dict[str, float]]:
    """Parse the --peers CLI value into (urls, offsets_by_url).

    Each comma-separated segment is either a bare peer URL
    (`ws://host:port`) or a peer URL with an inline per-scanner
    calibration offset (`ws://host:port=offset`). The offset is an
    additive correction in dB applied to that scanner's smoothed RSSI
    before election comparisons - see election.ScannerState and the
    "kill-test" cases in tests/test_election.py for why this matters
    (a radio that over-reports by +5dB will steal ownership from a
    truly-closer scanner unless its bias is cancelled with offset=-5.0).

    Bare-URL segments default to offset 0.0. The returned offsets dict
    is keyed by the exact URL string (URLs are known at startup; scanner
    ids are only learned on first message - see _register_peer_id).
    """
    urls: list[str] = []
    offsets: dict[str, float] = {}
    for part in raw.split(","):
        segment = part.strip()
        if not segment:
            continue
        url, sep, offset_str = segment.partition("=")
        url = url.strip()
        if not url:
            raise ValueError(f"Invalid --peers segment {segment!r}: missing URL.")
        offset = 0.0
        if sep:
            offset_str = offset_str.strip()
            try:
                offset = float(offset_str)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid --peers segment {segment!r}: offset {offset_str!r} is not a number."
                ) from exc
        urls.append(url)
        offsets[url] = offset
    if not urls:
        raise ValueError("--peers must contain at least one ws:// URL.")
    return urls, offsets


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aether Protocol mesh aggregator with leader election.")
    parser.add_argument(
        "--peers",
        required=True,
        help=(
            "Comma-separated peer scanner WebSocket URLs, optionally with an inline "
            "per-scanner calibration offset: ws://host:port or ws://host:port=offset. "
            "The offset (dB, additive) cancels radio miscalibration so a scanner that "
            "over-reports RSSI cannot steal ownership from a truly-closer scanner - see "
            "the kill-test cases in tests/test_election.py. "
            "Example: ws://127.0.0.1:9001,ws://127.0.0.1:9002=-5.0"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="WebSocket bind host for this aggregator's own server (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"WebSocket bind port (default: {DEFAULT_PORT}).")
    parser.add_argument("--tick-ms", type=int, default=DEFAULT_TICK_MS, help=f"Election tick interval in milliseconds (default: {DEFAULT_TICK_MS}).")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    peer_urls, peer_offsets = parse_peer_urls(args.peers)
    aggregator = Aggregator(peer_urls, args.host, args.port, args.tick_ms, peer_offsets)
    try:
        await aggregator.run()
    except KeyboardInterrupt:
        aggregator.stop()


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[aggregator] Stopped by user.")


if __name__ == "__main__":
    main()
