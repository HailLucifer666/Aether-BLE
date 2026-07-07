"""Aether Protocol Phase 2/3/4 - multi-scanner mesh aggregator with leader
election, portable conversation state, and tiered ranging.

Connects OUT as a WebSocket client to each configured peer scanner (real
bridge.py instances or simulated_scanner.py instances - this module never
imports bleak and has no notion of BLE hardware), maintains the latest
reading per scanner, runs election.py on a fixed tick, and serves its own
WebSocket endpoint broadcasting the election state for a dashboard to
consume. Also accepts a "wake" trigger (inbound WS message or a terminal
keypress) that resolves which scanner should react to a wake event given
current ownership.

Phase 3 adds: a "say" inbound message that generates a real TTS utterance
and assigns it to the current owner; and a four-phase handoff contract
(PREPARE -> TRANSFER -> CONFIRM -> RELEASE) that migrates the active
utterance to the new owner when ownership changes mid-sentence, so the
assistant literally finishes its sentence on the next device. TTS
generation (Phase 8: Piper, local/no-cloud) is optional - if it's missing
or fails, a synthetic utterance (no audio) keeps the migration demo
working.

Phase 8 adds: a "ask" inbound message that generates a real LLM reply
(local Ollama, via llm.py) for freeform user text, then feeds that reply
into the same _handle_say pipeline below - so an LLM-generated utterance is
owned/migrated identically to a manually-issued "say". _generate_speech now
tries Piper (local, no-cloud) first, preserving the exact synthetic-
fallback resilience the edge-tts path had.

Phase 4 adds tiered sensing: BLE alone (tier 1) resolves ~80% of
arbitrations; the remaining photo-finishes escalate to a near-ultrasound
chirp (tier 2) whose time-of-flight + room-containment bits settle ties
deterministically. The pure fusion logic lives in ranging.py; this module
owns the contest-detection hook (in the election tick loop), the ranging
loop that fires one chirp per contest episode, and the fusion into the
owner decision. Real audio capture is NOT implemented here - the ranging
source is an injectable callable (see Aggregator.__init__ ranging_source)
and defaults to a deterministic synthetic source. That callable is the
SEAM where a real microphone/capture backend plugs in without touching the
fusion logic.
"""

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import websockets

import ranging as ranging_mod
import llm
from piper_tts import PiperTTSError, estimate_duration_ms, pcm_to_wav_bytes, synthesize_pcm
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
from election import ChallengerState, ElectionTuning, Handoff, ScannerState, elect
from fusion_2d import FusionTracker
from layout import LayoutStore, LayoutValidationError, rssi_to_distance_m
from messages import build_conversation_message, build_election_message, build_position_message, build_ranging_message
from ranging import (
    ChirpMeasurement,
    ChirpResult,
    Contest,
    chirp_from_measurements,
    detect_contest,
    fuse,
    tof_to_distance,
)
from smoothing import apply_ema

DEFAULT_PORT = 8766
DEFAULT_TICK_MS = 400
PEER_RECONNECT_DELAY_SECONDS = 2.0
PRESENCE_TIMEOUT_SECONDS = 6.0
KEYPRESS_POLL_INTERVAL_SECONDS = 0.05
WAKE_KEYS = {b" ", b"\r"}

# Phase 8: cap on any text that reaches TTS synthesis via "say"/"ask" -
# without this, an unbounded string keeps Piper synthesizing (and the
# WS broadcast payload growing) for an attacker-controlled duration.
# Truncated (not rejected) to match the project's existing "never crash,
# always degrade" resilience pattern rather than dropping the utterance.
MAX_TTS_TEXT_LENGTH = 2000

# Phase 4: how many ticks a chirp resolution stays "fresh" for fusion before
# the aggregator demands a new chirp if the contest is still active. Two
# ticks matches the election hysteresis window (HYSTERESIS_CONSECUTIVE) so a
# chirp-backed override needs to survive the same scrutiny a hysteresis
# handoff would.
CHIRP_FRESH_TICKS = 2

# Phase 10: sane numeric bounds for the setTuning message. Matches the
# PRD's stated ranges - out-of-range or non-numeric input is logged and
# dropped rather than applied (never crash, always degrade).
MIN_HYSTERESIS_DB = 0.0
MAX_HYSTERESIS_DB = 20.0
MIN_CONSECUTIVE_TICKS = 1
MAX_CONSECUTIVE_TICKS = 20
MIN_CONTEST_MARGIN_DB = 0.0
MAX_CONTEST_MARGIN_DB = 20.0

# Security: placeDevice/setCalibration are reachable from any unauthenticated
# LAN peer (same trust model as say/wake/ask), and each one triggers a full
# rewrite of ~/.aether/layout.json via layout.py's LayoutStore._save() - a
# rapid-fire flood of either message meant unbounded disk I/O with no
# backpressure. This is a genuinely new resource-exhaustion vector Phase 10
# introduced (say/wake/ask are naturally bounded by TTS/LLM latency; a layout
# write is not), so it gets the same fix Phase 8's Wyoming Synthesize handler
# got: a minimum interval between accepted writes per message type, silently
# dropping anything that arrives too soon rather than queuing it - "degrade,
# never crash" per this codebase's existing style.
MIN_LAYOUT_WRITE_INTERVAL_SECONDS = 0.1


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


# ---------------------------------------------------------------------------
# Phase 4 - ranging source seam.
#
# A ranging source is a callable that turns a Contest into a ChirpResult.
# Real audio capture (phone mic listening for a coded 18-21 kHz chirp) plugs
# in here. The default below is a DETERMINISTIC SYNTHETIC source: it models
# each candidate scanner's distance from the phone as a fixed geometric
# layout (incumbent nearer, challenger farther) and synthesizes ToF
# measurements accordingly. It exists so the tier-2 logic, fusion, and
# dashboard viz are exercised end-to-end without a microphone - and so a
# future build can swap in `real_ranging_source` by passing a different
# callable to Aggregator.__init__ without touching ranging.py or the fusion
# logic. The synthetic source is honest about what it is: it never claims to
# have heard a real chirp; it produces the same ChirpResult shape a real
# source would.
#
# Geometric model: the incumbent is treated as ~1.5 m from the phone, the
# challenger as ~2.5 m. To convert distance to one-way ToF we invert
# tof_to_distance: tof_us = distance_m / speed_of_sound * 1e6.
# ---------------------------------------------------------------------------

RangingSource = "Callable[[Contest, int], ChirpResult | None]"


def _distance_to_tof_us(distance_m: float) -> float:
    """Inverse of ranging.tof_to_distance: meters -> one-way microseconds."""
    return (distance_m / 343.0) * 1_000_000.0


def synthetic_ranging_source(
    contest: Contest,
    tick: int,
    scanner_distances: dict[str, float] | None = None,
) -> ChirpResult | None:
    """Default ranging source: deterministic synthetic ToF from geometry.

    Returns a ChirpResult where each contest party that is "in range"
    produces a measurement whose ToF reflects its modelled distance from the
    phone. The model is intentionally simple and deterministic so the fusion
    logic and dashboard viz are exercised without a microphone. Both parties
    are modelled as hearing the chirp (same_room=True) at their configured
    distances; override `scanner_distances` to inject a wall (drop a party)
    or change the layout. This is the integration point a real mic capture
    backend replaces.
    """
    distances = scanner_distances or {
        contest.incumbent_id: 1.5,
        contest.challenger_id: 2.5,
    }
    measurements = []
    for scanner_id in (contest.incumbent_id, contest.challenger_id):
        distance_m = distances.get(scanner_id)
        if distance_m is None:
            continue  # modelled as behind a wall / out of beam: not heard
        measurements.append(
            ChirpMeasurement(
                scanner_id=scanner_id,
                tof_us=_distance_to_tof_us(distance_m),
                distance_m=distance_m,
            )
        )
    return chirp_from_measurements(tuple(measurements), contest, tick)


def make_geometry_ranging_source(geometry: dict[str, tuple[float, str]]):
    """Build a deterministic ranging source from a per-scanner geometry map.

    `geometry` maps scanner id -> (distance_meters, room) where room is "in"
    (same room as the phone, hears the chirp) or "out" (behind a wall / out of
    beam, does NOT hear the chirp). The returned callable is a thin wrapper over
    synthetic_ranging_source with this override semantics:

      - a scanner declared "in"  -> modelled at its declared distance;
      - a scanner declared "out" -> dropped (no measurement); this drop IS the
        wall, and the existing fuse() then returns chirp-room-containment when
        the BLE winner is the dropped one;
      - a scanner NOT in the map -> keeps the documented default (in-room at
        1.5m if it's the incumbent, 2.5m if the challenger).

    So `--ranging-geometry "B=4.0:out"` alone puts B behind a wall and leaves A
    at its default. This is the integration point the `--ranging-geometry` CLI
    flag wires into; it keeps synthetic_ranging_source itself unchanged (the
    default no-geometry path is byte-for-byte the prior behavior).
    """
    in_room_declarations = {
        scanner_id: distance_m
        for scanner_id, (distance_m, room) in geometry.items()
        if room == "in"
    }

    def source(contest: Contest, tick: int) -> ChirpResult | None:
        distances = {
            contest.incumbent_id: 1.5,
            contest.challenger_id: 2.5,
        }
        # "in" declarations override the default distance for the named
        # scanner; "out" declarations remove it entirely.
        distances.update(in_room_declarations)
        for scanner_id, (_distance_m, room) in geometry.items():
            if room == "out":
                distances.pop(scanner_id, None)
        return synthetic_ranging_source(contest, tick, scanner_distances=distances)

    return source


class Aggregator:
    def __init__(
        self,
        peer_urls: list[str],
        host: str,
        port: int,
        tick_ms: int,
        peer_offsets: dict[str, float] | None = None,
        ranging_source: "RangingSource | None" = None,
        layout_store: "LayoutStore | None" = None,
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

        # Phase 4: tiered ranging. The ranging source is the SEAM where real
        # audio capture plugs in; it defaults to the deterministic synthetic
        # source (see synthetic_ranging_source). _active_contest is the most
        # recent detect_contest() result while a contest is live; it clears
        # to None once the contest resolves. _last_chirp is the most-recent
        # ChirpResult still within CHIRP_FRESH_TICKS of being produced.
        # _last_fusion_reason is broadcast every ranging message so the
        # dashboard can label how the latest owner decision was reached.
        # _pending_ranging_event mirrors the wakeOutcome/conversationEvent
        # one-shot pattern: set when a chirp fires, broadcast once, cleared.
        self._ranging_source = ranging_source or synthetic_ranging_source
        self._active_contest: Contest | None = None
        self._last_chirp: ChirpResult | None = None
        self._last_chirp_tick: int = -1
        self._last_fusion_reason: str = "ble-only"
        self._pending_ranging_event: dict | None = None
        self._ranging_dirty = False

        # Phase 10: persisted scanner placement/calibration + the 2-D fusion
        # tracker (fills fusion_2d.py's previously-dormant call site). The
        # layout store defaults to layout.py's own default path
        # (~/.aether/layout.json), matching room_adjacency.py's convention.
        # _tuning is the mutable live-tunable election/contest parameters
        # (Phase 10's setTuning message target); ranging.py's module-level
        # CONTEST_MARGIN_DB is kept in lockstep by _handle_set_tuning via
        # direct module-attribute assignment, since detect_contest() reads
        # that module global and its algorithm is not touched this phase.
        self._layout = layout_store or LayoutStore()
        self._fusion_tracker = FusionTracker()
        self._tuning = ElectionTuning()
        # Rate-limit gate for the two message types that trigger a full
        # layout.json rewrite - see MIN_LAYOUT_WRITE_INTERVAL_SECONDS above.
        self._last_layout_write_monotonic: float | None = None

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
            result = elect(self._owner, scanners, self._challenger, self._tuning)
            ble_owner = result.new_owner
            self._challenger = result.challenger

            # Phase 4: detect a BLE photo-finish and (when one is active and
            # a fresh chirp is available) fuse the chirp into the owner
            # decision. The contest object is computed every tick so a fresh
            # escalation is noticed immediately; the chirp is produced by the
            # ranging loop (one per contest episode) and consumed here.
            contest = detect_contest(ble_owner, scanners, self._tick)
            self._active_contest = contest
            chirp = self._fresh_chirp()
            fusion = fuse(ble_owner, scanners, contest, chirp)
            self._last_fusion_reason = fusion.reason
            self._owner = fusion.owner

            # A tier-2 override counts as a handoff for logging/broadcast
            # purposes (the owner visibly changed), even though elect()
            # itself didn't emit a Handoff - this is the whole point of
            # tier 2: it produces handoffs tier 1 cannot.
            if fusion.overridden_by_ranging and previous_owner is not None and fusion.owner != previous_owner:
                self._record_handoff(
                    Handoff(from_id=previous_owner, to_id=fusion.owner)
                )
                self._ranging_dirty = True
            elif result.handoff is not None:
                self._record_handoff(result.handoff)

            # Phase 3: if ownership changed while an utterance is active and
            # being spoken by the outgoing owner, kick off the conversation
            # handoff FSM. Catch every ownership change (not just the
            # Handoff-record path), since first-contact acquisition also
            # changes the owner without emitting a Handoff from elect().
            if (
                previous_owner is not None
                and self._owner is not None
                and previous_owner != self._owner
            ):
                self._maybe_begin_conversation_handoff(previous_owner, self._owner)

            # Surface ranging activity whenever a contest is live or a fresh
            # chirp exists, so the dashboard's ranging panel reflects state.
            if contest is not None or chirp is not None:
                self._ranging_dirty = True

            # Phase 10: advisory 2-D fusion. Purely additive - never consulted
            # by the owner decision above (fusion.owner already resolved).
            # Feeds the elected/fused owner's identity as the tracked
            # "user_id" (this codebase's Phase 9 ceiling: one track per
            # beacon identity, and the owning scanner is the best available
            # proxy for "the phone" until per-device identity exists).
            if self._owner is not None:
                self._update_fusion_track(self._owner, scanners, chirp)

    def _update_fusion_track(
        self, user_id: str, scanners: list[ScannerState], chirp: ChirpResult | None
    ) -> None:
        """Convert each present scanner's calibrated RSSI to a distance (via
        this scanner's layout.py calibration) and fold it into the
        FusionTracker, matching fusion_2d.update_from_scanner_distances'
        chirp_scanner_id contract: the freshest chirp's own ToF-derived
        distance_m (cm-grade precision) overrides the RSSI-derived distance
        for whichever scanner produced that chirp measurement, only while
        that chirp is still fresh (the caller already gates `chirp` via
        _fresh_chirp() - the same freshness check _current_ranging_message
        uses).

        Scanners with no placed position or no calibration are silently
        skipped (no geometry/model to convert their RSSI against yet) - this
        never raises, so a not-yet-configured layout can't stall the tick
        loop.
        """
        scanner_positions = self._layout.get_scanner_positions()
        if not scanner_positions:
            return  # nothing placed yet - fusion has no geometry to run on

        scanner_distances: dict[str, float] = {}
        for scanner in scanners:
            if not scanner.present:
                continue
            calibrated = scanner.calibrated_rssi()
            if calibrated is None:
                continue
            if scanner.id not in scanner_positions:
                continue
            calibration = self._layout.get_calibration(scanner.id)
            if calibration is None:
                continue
            scanner_distances[scanner.id] = rssi_to_distance_m(calibrated, calibration)

        chirp_scanner_id = None
        if chirp is not None:
            for measurement in chirp.measurements:
                if measurement.scanner_id in scanner_positions:
                    # Chirp ToF distance is much more precise than the RSSI
                    # conversion above - override that scanner's distance
                    # reading with the chirp's own measurement before fusion,
                    # and mark it so update() applies chirp-grade noise.
                    scanner_distances[measurement.scanner_id] = measurement.distance_m
                    chirp_scanner_id = measurement.scanner_id
                    break

        if not scanner_distances:
            return

        self._fusion_tracker.update(
            user_id,
            self._tick,
            scanner_positions,
            scanner_distances,
            chirp_scanner_id=chirp_scanner_id,
        )

    def _fresh_chirp(self) -> ChirpResult | None:
        """Return _last_chirp if it's still within CHIRP_FRESH_TICKS, else None.

        A chirp resolution is only useful for fusion while it's recent - the
        phone may have moved since. Older chirps are discarded so a stale
        reading can't keep overriding the live BLE election indefinitely.
        """
        if self._last_chirp is None:
            return None
        if self._tick - self._last_chirp_tick > CHIRP_FRESH_TICKS:
            return None
        return self._last_chirp

    async def _ranging_loop(self) -> None:
        """Fire one tier-2 chirp per contest episode.

        Triggered when a contest is active AND we don't already have a fresh
        chirp for the same contest pair. The ranging source (default:
        deterministic synthetic; swappable for real mic capture) produces the
        ChirpResult, which the election tick loop then consumes via
        _fresh_chirp() + fuse(). One chirp per episode keeps the duty cycle
        low (the on-demand tier-2 promise): once a chirp is fresh, subsequent
        ticks reuse it until it expires; a brand-new contest (different
        incumbent/challenger pair) demands a new chirp.
        """
        while not self._stop_event.is_set():
            await asyncio.sleep(self.tick_interval_seconds)
            contest = self._active_contest
            if contest is None:
                continue
            if self._has_fresh_chirp_for(contest):
                continue

            chirp = self._ranging_source(contest, self._tick)
            if chirp is None:
                continue
            self._last_chirp = chirp
            self._last_chirp_tick = self._tick
            self._stage_ranging_event(contest, chirp)
            self._ranging_dirty = True
            print(
                f"[aggregator] RANGING chirp tick={self._tick} "
                f"{contest.incumbent_id}<->{contest.challenger_id} "
                f"winner={chirp.winner_id} same_room={chirp.same_room}"
            )

    def _has_fresh_chirp_for(self, contest: Contest) -> bool:
        """Is the most-recent chirp still fresh AND covering this contest pair?

        A chirp covers the contest if both contest parties appear in its
        measurements (same_room True path) - i.e. the chirp already settled
        exactly this photo-finish. If only one party (or neither) was heard,
        the situation may have changed, so we allow a refire.
        """
        existing = self._last_chirp
        if existing is None:
            return False
        if self._tick - self._last_chirp_tick > CHIRP_FRESH_TICKS:
            return False
        heard = {m.scanner_id for m in existing.measurements}
        return {contest.incumbent_id, contest.challenger_id} <= heard

    def _stage_ranging_event(self, contest: Contest, chirp: ChirpResult) -> None:
        self._pending_ranging_event = {
            "phase": "CHIRP",
            "contestIncumbent": contest.incumbent_id,
            "contestChallenger": contest.challenger_id,
            "winnerId": chirp.winner_id,
            "sameRoom": chirp.same_room,
            "atTick": self._tick,
        }

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

        Tries Piper (local, ONNX-based neural TTS, no cloud) first. On ANY
        failure (model missing, subprocess/inference error, malformed text),
        falls back to a synthetic utterance that has no audio but advances
        on the same progress clock - so the handoff-migration demo still
        works offline. Either way the conversation FSM and broadcast are
        identical; only the audio payload differs. Reused by _handle_ask
        (Phase 8) so an LLM-generated reply is spoken/owned identically to a
        manually-issued "say".
        """
        owner = self._owner
        if owner is None:
            print("[aggregator] SAY ignored - no current owner")
            return

        text = text.strip()
        if not text:
            return
        if len(text) > MAX_TTS_TEXT_LENGTH:
            text = text[:MAX_TTS_TEXT_LENGTH]

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
        kind = "synthetic" if is_synthetic else "piper"
        print(
            f"[aggregator] SAY tick={self._tick} owner={owner} "
            f"({kind}, {duration_ms}ms, {len(text)} chars): {text[:60]!r}"
        )

    async def _handle_ask(self, text: str) -> None:
        """Phase 8: generate an LLM reply for `text` and speak it via the
        existing _handle_say pipeline, additive alongside the manual "say"
        path (which stays exactly as-is for debugging/demos).

        Builds transcript context from the current conversation transcript,
        calls llm.generate_reply (local Ollama). On any LLM failure, falls
        back to llm.FALLBACK_REPLY rather than dropping the ask entirely -
        the conversation FSM and TTS pipeline still run, just with a fixed
        apology string instead of a generated reply, mirroring the same
        "never crash, always degrade" resilience _generate_speech already
        has for TTS failures.
        """
        text = text.strip()
        if not text:
            return

        transcript_context = [
            {"role": entry.role, "text": entry.text} for entry in self._conversation.transcript
        ]

        try:
            reply_text = await asyncio.to_thread(llm.generate_reply, transcript_context, text)
        except llm.LLMError as exc:
            print(f"[aggregator] ASK llm.generate_reply failed ({exc}); using fallback reply.")
            reply_text = llm.FALLBACK_REPLY

        await self._handle_say(reply_text)

    async def _generate_speech(self, text: str) -> tuple[str | None, int, bool]:
        """Returns (audio_base64_data_url, duration_ms, is_synthetic).

        Tries Piper (local, ONNX-based neural TTS, no cloud) first via the
        shared piper_tts.py module - the same path wyoming_satellite.py uses
        for HA-driven synthesis. On ANY failure (model missing, synthesis
        error) returns (None, synthetic_estimate, True), preserving the
        exact resilience property the old edge-tts path had: a TTS failure
        must never crash the aggregator or block the conversation FSM.
        """
        try:
            pcm_bytes, sample_rate = await asyncio.to_thread(synthesize_pcm, text)
        except PiperTTSError as exc:
            print(f"[aggregator] Piper TTS failed ({exc}); using synthetic utterance.")
            return None, self._synthetic_duration(text), True

        wav_bytes = pcm_to_wav_bytes(pcm_bytes, sample_rate)
        duration_ms = max(self._synthetic_duration(text), estimate_duration_ms(len(pcm_bytes), sample_rate))
        audio_b64 = "data:audio/wav;base64," + base64.b64encode(wav_bytes).decode("ascii")
        return audio_b64, duration_ms, False

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
        elif msg_type == "ask":
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                # LLM generation + TTS are both async; dispatch as a task so
                # the inbound handler returns promptly, same pattern as "say".
                asyncio.ensure_future(self._handle_ask(text))
        elif msg_type == "placeDevice":
            self._handle_place_device(message)
        elif msg_type == "setCalibration":
            self._handle_set_calibration(message)
        elif msg_type == "setTuning":
            self._handle_set_tuning(message)

    # -- Phase 10: layout placement / calibration / tuning inbound messages -

    def _layout_write_allowed(self) -> bool:
        """Rate-limit gate shared by placeDevice/setCalibration - see
        MIN_LAYOUT_WRITE_INTERVAL_SECONDS. Stamps the timestamp only when the
        write is allowed (stamp-before-write ordering, same as Phase 8's
        Wyoming Synthesize fix), so a burst never lets two writes slip
        through within the same interval."""
        now = time.monotonic()
        if (
            self._last_layout_write_monotonic is not None
            and now - self._last_layout_write_monotonic < MIN_LAYOUT_WRITE_INTERVAL_SECONDS
        ):
            return False
        self._last_layout_write_monotonic = now
        return True

    def _handle_place_device(self, message: dict) -> None:
        """placeDevice {scannerId, x, y} - LAN-trust model, same as say/wake.

        Validates via layout.py before writing; on any invalid input, logs a
        warning and drops the message (never crashes the tick loop, never
        corrupts the persisted layout file). Rate-limited per
        MIN_LAYOUT_WRITE_INTERVAL_SECONDS - a flood of placeDevice messages
        is silently dropped rather than each one rewriting layout.json.
        """
        if not self._layout_write_allowed():
            return
        scanner_id = message.get("scannerId")
        x = message.get("x")
        y = message.get("y")
        if not isinstance(scanner_id, str) or not scanner_id:
            print(f"[aggregator] WARNING: placeDevice missing/invalid scannerId; dropping. {message!r}")
            return
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            print(f"[aggregator] WARNING: placeDevice x/y must be numbers; dropping. {message!r}")
            return
        try:
            self._layout.set_position(scanner_id, float(x), float(y))
        except LayoutValidationError as exc:
            print(f"[aggregator] WARNING: placeDevice rejected ({exc}); dropping.")
            return
        print(f"[aggregator] LAYOUT placeDevice {scanner_id} -> ({x}, {y})")

    def _handle_set_calibration(self, message: dict) -> None:
        """setCalibration {scannerId, rssiAt1m, pathLossExponent} - same
        trust model and drop-on-invalid discipline as _handle_place_device.
        Shares the same rate-limit gate (both hit the same layout.json)."""
        if not self._layout_write_allowed():
            return
        scanner_id = message.get("scannerId")
        rssi_at_1m = message.get("rssiAt1m")
        path_loss_exponent = message.get("pathLossExponent")
        if not isinstance(scanner_id, str) or not scanner_id:
            print(f"[aggregator] WARNING: setCalibration missing/invalid scannerId; dropping. {message!r}")
            return
        if not isinstance(rssi_at_1m, (int, float)) or not isinstance(path_loss_exponent, (int, float)):
            print(f"[aggregator] WARNING: setCalibration rssiAt1m/pathLossExponent must be numbers; dropping. {message!r}")
            return
        try:
            self._layout.set_calibration(scanner_id, float(rssi_at_1m), float(path_loss_exponent))
        except LayoutValidationError as exc:
            print(f"[aggregator] WARNING: setCalibration rejected ({exc}); dropping.")
            return
        print(
            f"[aggregator] LAYOUT setCalibration {scanner_id} -> "
            f"rssiAt1m={rssi_at_1m} pathLossExponent={path_loss_exponent}"
        )

    def _handle_set_tuning(self, message: dict) -> None:
        """setTuning {hysteresisDb, consecutiveTicks, contestMarginDb}.

        Validates numeric ranges (hysteresisDb in [0,20], consecutiveTicks a
        positive int in [1,20], contestMarginDb in [0,20]) before mutating
        anything. On any invalid field, logs a warning and drops the ENTIRE
        message (partial application would leave tuning in a state the
        sender never asked for) - mirrors this codebase's "never crash,
        always degrade" resilience style.

        Applies to self._tuning (consumed by election.elect() every tick)
        AND to ranging.CONTEST_MARGIN_DB (a module-level global that
        detect_contest() reads directly; ranging.py's own contest-detection
        algorithm is unchanged - only where that one number comes from).
        """
        hysteresis_db = message.get("hysteresisDb")
        consecutive_ticks = message.get("consecutiveTicks")
        contest_margin_db = message.get("contestMarginDb")

        if not isinstance(hysteresis_db, (int, float)) or isinstance(hysteresis_db, bool):
            print(f"[aggregator] WARNING: setTuning hysteresisDb must be a number; dropping. {message!r}")
            return
        if not isinstance(consecutive_ticks, int) or isinstance(consecutive_ticks, bool):
            print(f"[aggregator] WARNING: setTuning consecutiveTicks must be an int; dropping. {message!r}")
            return
        if not isinstance(contest_margin_db, (int, float)) or isinstance(contest_margin_db, bool):
            print(f"[aggregator] WARNING: setTuning contestMarginDb must be a number; dropping. {message!r}")
            return

        if not (MIN_HYSTERESIS_DB <= hysteresis_db <= MAX_HYSTERESIS_DB):
            print(
                f"[aggregator] WARNING: setTuning hysteresisDb={hysteresis_db} out of "
                f"range [{MIN_HYSTERESIS_DB}, {MAX_HYSTERESIS_DB}]; dropping."
            )
            return
        if not (MIN_CONSECUTIVE_TICKS <= consecutive_ticks <= MAX_CONSECUTIVE_TICKS):
            print(
                f"[aggregator] WARNING: setTuning consecutiveTicks={consecutive_ticks} out of "
                f"range [{MIN_CONSECUTIVE_TICKS}, {MAX_CONSECUTIVE_TICKS}]; dropping."
            )
            return
        if not (MIN_CONTEST_MARGIN_DB <= contest_margin_db <= MAX_CONTEST_MARGIN_DB):
            print(
                f"[aggregator] WARNING: setTuning contestMarginDb={contest_margin_db} out of "
                f"range [{MIN_CONTEST_MARGIN_DB}, {MAX_CONTEST_MARGIN_DB}]; dropping."
            )
            return

        self._tuning = ElectionTuning(
            hysteresis_db=float(hysteresis_db),
            hysteresis_consecutive=int(consecutive_ticks),
            contest_margin_db=float(contest_margin_db),
        )
        # ranging.detect_contest() reads this module-level global directly;
        # keep it in lockstep so setTuning takes effect immediately without
        # touching ranging.py's contest-detection algorithm.
        ranging_mod.CONTEST_MARGIN_DB = float(contest_margin_db)
        print(
            f"[aggregator] TUNING updated hysteresisDb={hysteresis_db} "
            f"consecutiveTicks={consecutive_ticks} contestMarginDb={contest_margin_db}"
        )

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
            ranging_message = self._current_ranging_message()
            # rangingEvent is one-shot too.
            self._pending_ranging_event = None
            position_message = self._current_position_message()
            if not self.clients:
                # Even with no clients, clear the dirty flags so they don't
                # queue up an unnecessary broadcast on the next connect.
                self._conversation_dirty = False
                self._ranging_dirty = False
                continue
            payloads = [json.dumps(election_message)]
            # Only attach the conversation message when there's something to
            # say (active utterance, non-empty transcript, or a pending event).
            # This keeps the wire quiet when no conversation has started.
            if conversation_message is not None:
                payloads.append(json.dumps(conversation_message))
            # Only attach the ranging message when tier 2 has been invoked
            # (an active contest, a fresh chirp, or a pending chirp event).
            # This keeps the wire quiet in the common uncontested case.
            if ranging_message is not None:
                payloads.append(json.dumps(ranging_message))
            # Only attach the position message when a fusion track exists for
            # the current owner. This keeps the wire quiet for a fresh
            # install with no placed layout (no track is ever created).
            if position_message is not None:
                payloads.append(json.dumps(position_message))
            self._conversation_dirty = False
            self._ranging_dirty = False
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

    def _current_ranging_message(self) -> dict | None:
        """Build the tier-2 ranging broadcast, or None if tier 2 is idle.

        Suppresses the message entirely when there is no active contest, no
        fresh chirp, and no pending one-shot ranging event - so before any
        photo-finish escalates, the wire carries only election (and possibly
        conversation) messages. Once a contest fires, the message flows every
        tick so the dashboard's ranging panel reflects live state.
        """
        contest = self._active_contest
        chirp = self._fresh_chirp()
        if contest is None and chirp is None and self._pending_ranging_event is None:
            return None

        contest_dict = None
        if contest is not None:
            contest_dict = {
                "incumbentId": contest.incumbent_id,
                "challengerId": contest.challenger_id,
                "incumbentRssi": contest.incumbent_rssi,
                "challengerRssi": contest.challenger_rssi,
                "atTick": contest.at_tick,
            }
        chirp_dict = None
        if chirp is not None:
            chirp_dict = {
                "measurements": [
                    {
                        "scannerId": m.scanner_id,
                        "tofUs": m.tof_us,
                        "distanceM": m.distance_m,
                    }
                    for m in chirp.measurements
                ],
                "winnerId": chirp.winner_id,
                "sameRoom": chirp.same_room,
                "resolvedTick": chirp.resolved_tick,
            }
        return build_ranging_message(
            contest=contest_dict,
            chirp=chirp_dict,
            fusion_reason=self._last_fusion_reason,
            ranging_event=self._pending_ranging_event,
        )

    def _current_position_message(self) -> dict | None:
        """Build the Phase 10 position broadcast, or None if there's no
        track yet for the current owner.

        Mirrors _current_conversation_message/_current_ranging_message's
        exact suppression style: before any layout is placed (or before the
        first owner acquisition), there is no track, so this returns None
        and the wire stays quiet - a fresh install with no placed layout
        never emits a position message.
        """
        owner = self._owner
        if owner is None:
            return None
        track = self._fusion_tracker.tracks.get(owner)
        if track is None:
            return None
        x, y = track.position
        return build_position_message(
            user_id=owner,
            x=x,
            y=y,
            uncertainty_radius_m=track.position_uncertainty_radius_m,
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
        ranging_task = asyncio.ensure_future(self._ranging_loop())
        broadcast_task = asyncio.ensure_future(self._broadcast_loop())
        readout_task = asyncio.ensure_future(self._terminal_readout_loop())
        keypress_task = asyncio.ensure_future(self._stdin_keypress_loop())

        background_tasks = [
            tick_task,
            conversation_task,
            ranging_task,
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


def parse_ranging_geometry(raw: str) -> dict[str, tuple[float, str]]:
    """Parse the --ranging-geometry CLI value into a per-scanner geometry map.

    Format: a comma-separated list of `id=distance:room` segments, where
    `distance` is the scanner's distance from the phone in meters and `room`
    is `in` (same room as the phone - hears the chirp) or `out` (behind a wall
    / out of beam - does NOT hear the chirp). Examples:
        A=1.5:in,B=2.5:in        both in-room at the given distances
        A=1.5:in,B=2.5:out       B behind a wall -> room-containment override
        SIM-B=4.0:out            only declare B; A falls back to its default

    Returns a dict {scanner_id: (distance_m, "in" | "out")}. Empty segments are
    skipped; an out-of-set room token, a non-numeric distance, or a missing
    piece raises ValueError with the offending segment quoted. The result is
    consumed by make_geometry_ranging_source, which drops every "out" scanner
    before handing the layout to synthetic_ranging_source - that drop IS the
    wall in the demo (the existing fuse() then returns chirp-room-containment).
    """
    geometry: dict[str, tuple[float, str]] = {}
    for part in raw.split(","):
        segment = part.strip()
        if not segment:
            continue
        id_str, sep, rest = segment.partition("=")
        id_str = id_str.strip()
        if not sep or not id_str:
            raise ValueError(
                f"Invalid --ranging-geometry segment {segment!r}: expected 'id=distance:room'."
            )
        rest = rest.strip()
        distance_str, colon, room = rest.partition(":")
        if not colon:
            raise ValueError(
                f"Invalid --ranging-geometry segment {segment!r}: missing ':room' (use :in or :out)."
            )
        distance_str = distance_str.strip()
        room = room.strip()
        if room not in ("in", "out"):
            raise ValueError(
                f"Invalid --ranging-geometry segment {segment!r}: room {room!r} must be 'in' or 'out'."
            )
        try:
            distance_m = float(distance_str)
        except ValueError as exc:
            raise ValueError(
                f"Invalid --ranging-geometry segment {segment!r}: distance {distance_str!r} is not a number."
            ) from exc
        geometry[id_str] = (distance_m, room)
    return geometry


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
    parser.add_argument(
        "--ranging-geometry",
        default=None,
        help=(
            "Optional per-scanner tier-2 geometry for the synthetic ranging "
            "source, as a comma-separated list of id=distance_meters:room where "
            "room is 'in' (same room as the phone, hears the chirp) or 'out' "
            "(behind a wall, does NOT hear the chirp). A scanner marked 'out' "
            "is dropped from chirp measurements; when the BLE winner is the one "
            "dropped, fusion overrides it with chirp-room-containment - the "
            "Phase 4 wall demo. Default (flag absent): both contest parties in "
            "room at the documented distances (1.5m incumbent, 2.5m challenger). "
            "Example: --ranging-geometry \"A=1.5:in,B=2.5:out\". Mutually "
            "exclusive with --ranging-real-chirp (Phase 9)."
        ),
    )
    parser.add_argument(
        "--ranging-real-chirp",
        default=None,
        metavar="SCANNER_ID",
        help=(
            "Phase 9: use a REAL acoustic chirp (real_ranging_source.py) "
            "instead of the synthetic/geometry ranging source, via this "
            "process's local speaker+mic (sounddevice). SCANNER_ID names "
            "which scanner id this local audio hardware represents in every "
            "contest - see real_ranging_source.py's module docstring for the "
            "single-mic physical model this implies. Mutually exclusive "
            "with --ranging-geometry. Not verifiable by an agent without "
            "real hardware in the same room - see docs/phase9/PRD.md."
        ),
    )
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    peer_urls, peer_offsets = parse_peer_urls(args.peers)
    if args.ranging_geometry is not None and args.ranging_real_chirp is not None:
        raise ValueError("--ranging-geometry and --ranging-real-chirp are mutually exclusive.")
    ranging_source = None
    if args.ranging_geometry is not None:
        geometry = parse_ranging_geometry(args.ranging_geometry)
        ranging_source = make_geometry_ranging_source(geometry)
    elif args.ranging_real_chirp is not None:
        from real_ranging_source import make_real_ranging_source

        ranging_source = make_real_ranging_source(local_scanner_id=args.ranging_real_chirp)
    aggregator = Aggregator(
        peer_urls, args.host, args.port, args.tick_ms, peer_offsets, ranging_source=ranging_source
    )
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
