"""Pure conversation-state + handoff FSM logic for the Aether Protocol.

Mirrors election.py's discipline: zero I/O imports (no bleak, websockets,
asyncio, no wall-clock reads, no edge_tts), so this module is a deterministic
function of its inputs and can be unit-tested tick-by-tick without any
hardware or network.

The model: exactly one scanner "speaks" an utterance at a time. When the
elected owner changes mid-utterance, a four-phase contract migrates the
active utterance to the new owner so the assistant literally finishes its
sentence on the next device. The ~400ms pause during TRANSFER/CONFIRM is
the visceral "the sentence moved" moment.

Phase sequence on handoff:
    IDLE -> PREPARE -> TRANSFER -> CONFIRM -> RELEASE -> IDLE

Rules:
    - A handoff only starts the FSM if an utterance is active AND the
      scanner currently speaking is the one losing ownership. A handoff
      with no active utterance is a no-op (nothing to migrate).
    - PREPARE: notify the new owner to ready itself. speaking_scanner is
      still the OLD owner.
    - TRANSFER: the dashboard pauses audio and records the playhead
      offset. speaking_scanner is still the OLD owner.
    - CONFIRM: speaking_scanner flips to the NEW owner. The dashboard
      seeks the audio to the recorded offset.
    - RELEASE: the dashboard resumes playback from the offset, now
      attributed to the NEW owner.
    - IDLE: phase fields clear; the utterance continues normally under
      the new owner.
"""

from dataclasses import dataclass, field, replace

# Phase name literals - plain strings to match the wire convention
# (the dashboard's locked schema uses string unions, not enums).
PHASE_IDLE = "IDLE"
PHASE_PREPARE = "PREPARE"
PHASE_TRANSFER = "TRANSFER"
PHASE_CONFIRM = "CONFIRM"
PHASE_RELEASE = "RELEASE"

# Wall-clock-equivalent duration spent in each non-IDLE phase. The FSM is
# driven by tick counts supplied by the caller; these constants define how
# many milliseconds each phase occupies so callers can convert ticks -> ms
# or vice versa. Kept here (not in the aggregator) so the FSM's pacing is
# defined alongside its logic and is unit-testable.
PHASE_DURATIONS_MS = {
    PHASE_PREPARE: 200,
    PHASE_TRANSFER: 200,
    PHASE_CONFIRM: 200,
    PHASE_RELEASE: 200,
}

# Rough estimate for synthetic utterances: ~10 chars/sec for neural TTS,
# so ~60ms per character. Used only when edge_tts is unavailable.
SYNTHETIC_MS_PER_CHAR = 60

# The ordered sequence of phases a handoff cycles through. Defined once so
# advance_phase and tests share a single source of truth.
_HANDOFF_SEQUENCE = [PHASE_PREPARE, PHASE_TRANSFER, PHASE_CONFIRM, PHASE_RELEASE]


@dataclass(frozen=True)
class TranscriptEntry:
    """One line of conversation history.

    `role` is "user" (what the user typed) or "assistant" (the generated
    utterance). Kept on the transcript after the utterance finishes.
    """

    id: int
    scanner: str
    role: str
    text: str
    ts: str


@dataclass(frozen=True)
class Utterance:
    """The active utterance being spoken by some scanner.

    `audio_base64` is a data: URL payload (or None in synthetic mode where
    there is no real audio). `offset_ms` is the playhead position within
    the utterance - updated by the dashboard, mirrored here for broadcast.
    `is_synthetic` marks the offline fallback path (no edge_tts audio).
    """

    text: str
    audio_base64: str | None
    duration_ms: int
    offset_ms: int
    is_synthetic: bool
    started_at_tick: int


@dataclass(frozen=True)
class ConversationState:
    """Full mutable conversation state. Frozen + replace() for the same
    auditability reasons as election.py's ChallengerState."""

    transcript: tuple[TranscriptEntry, ...] = ()
    utterance: Utterance | None = None
    speaking_scanner: str | None = None
    phase: str = PHASE_IDLE
    phase_from: str | None = None
    phase_to: str | None = None
    phase_started_at_tick: int = 0
    # Monotonic id counter for transcript entries. Carried in state so the
    # next entry's id is deterministic across replacements.
    next_transcript_id: int = 1

    @classmethod
    def empty(cls) -> "ConversationState":
        return cls()


def start_utterance(
    state: ConversationState,
    scanner: str,
    text: str,
    audio_base64: str | None,
    duration_ms: int,
    is_synthetic: bool,
    tick: int,
    ts: str,
    role: str = "assistant",
) -> ConversationState:
    """Begin a new utterance spoken by `scanner`.

    Appends the utterance text to the transcript (as `role`) and sets it as
    active. Replaces any in-flight utterance (build 1: no queueing). The
    phase is forced to IDLE - starting a new utterance cancels any in-flight
    handoff, which is the right call because a fresh say mid-handoff would
    otherwise race the FSM.
    """
    entry = TranscriptEntry(id=state.next_transcript_id, scanner=scanner, role=role, text=text, ts=ts)
    utterance = Utterance(
        text=text,
        audio_base64=audio_base64,
        duration_ms=max(0, duration_ms),
        offset_ms=0,
        is_synthetic=is_synthetic,
        started_at_tick=tick,
    )
    return replace(
        state,
        transcript=state.transcript + (entry,),
        utterance=utterance,
        speaking_scanner=scanner,
        phase=PHASE_IDLE,
        phase_from=None,
        phase_to=None,
        phase_started_at_tick=0,
        next_transcript_id=state.next_transcript_id + 1,
    )


def has_active_utterance(state: ConversationState) -> bool:
    return state.utterance is not None


def begin_handoff(
    state: ConversationState,
    from_scanner: str,
    to_scanner: str,
    tick: int,
) -> ConversationState:
    """Begin a handoff of the active utterance from `from_scanner` to `to_scanner`.

    No-op (returns state unchanged) if:
      - there is no active utterance (nothing to migrate), OR
      - speaking_scanner is not `from_scanner` (the handoff doesn't affect
        the active utterance), OR
      - a handoff FSM is already running (phase != IDLE).

    Otherwise transitions to PREPARE.
    """
    if state.utterance is None:
        return state
    if state.speaking_scanner != from_scanner:
        return state
    if state.phase != PHASE_IDLE:
        return state
    return replace(
        state,
        phase=PHASE_PREPARE,
        phase_from=from_scanner,
        phase_to=to_scanner,
        phase_started_at_tick=tick,
    )


def _phase_complete(state: ConversationState, now_tick: int, tick_ms: int) -> bool:
    """Has the current phase occupied its full duration by `now_tick`?"""
    elapsed_ms = (now_tick - state.phase_started_at_tick) * tick_ms
    return elapsed_ms >= PHASE_DURATIONS_MS.get(state.phase, 0)


def advance_phase(state: ConversationState, now_tick: int, tick_ms: int) -> ConversationState:
    """Advance the FSM one step if the current phase has elapsed.

    PREPARE -> TRANSFER -> CONFIRM -> RELEASE -> IDLE.

    On entering CONFIRM, speaking_scanner flips to phase_to (the new owner
    now "owns" the rest of the utterance). On entering IDLE the phase
    fields clear but the utterance remains active under the new owner.

    No-op if phase is IDLE or the current phase hasn't elapsed yet.
    """
    if state.phase == PHASE_IDLE:
        return state
    if not _phase_complete(state, now_tick, tick_ms):
        return state

    next_phase = _next_phase(state.phase)
    if next_phase == PHASE_IDLE:
        # Handoff complete: clear phase fields, keep the (already-flipped)
        # speaking_scanner and the active utterance.
        return replace(
            state,
            phase=PHASE_IDLE,
            phase_from=None,
            phase_to=None,
            phase_started_at_tick=0,
        )

    new_speaking = state.speaking_scanner
    # Flip ownership at the CONFIRM boundary - by then the dashboard has
    # paused audio (TRANSFER) and the new owner is committed.
    if next_phase == PHASE_CONFIRM:
        new_speaking = state.phase_to
    return replace(
        state,
        phase=next_phase,
        speaking_scanner=new_speaking,
        phase_started_at_tick=now_tick,
    )


def _next_phase(phase: str) -> str:
    try:
        idx = _HANDOFF_SEQUENCE.index(phase)
    except ValueError:
        return PHASE_IDLE
    if idx + 1 >= len(_HANDOFF_SEQUENCE):
        return PHASE_IDLE
    return _HANDOFF_SEQUENCE[idx + 1]


def finish_utterance(state: ConversationState) -> ConversationState:
    """Clear the active utterance when playback completes. Transcript stays."""
    if state.utterance is None:
        return state
    return replace(
        state,
        utterance=None,
        speaking_scanner=None,
        # If a handoff was somehow mid-flight, cancel it - there's nothing
        # left to migrate.
        phase=PHASE_IDLE,
        phase_from=None,
        phase_to=None,
        phase_started_at_tick=0,
    )


def utterance_progress(state: ConversationState, now_tick: int, tick_ms: int) -> float:
    """Estimated playback progress 0.0..1.0 for the active utterance.

    During a handoff the progress is frozen at the TRANSFER boundary so the
    broadcast doesn't appear to keep advancing while audio is paused. Returns
    0.0 if there is no active utterance.
    """
    if state.utterance is None:
        return 0.0
    # Freeze progress while the FSM is mid-handoff (audio is paused).
    if state.phase in (PHASE_TRANSFER, PHASE_CONFIRM):
        return _clamp_progress(state.utterance.offset_ms, state.utterance.duration_ms)
    elapsed_ms = (now_tick - state.utterance.started_at_tick) * tick_ms
    return _clamp_progress(elapsed_ms, state.utterance.duration_ms)


def _clamp_progress(elapsed_ms: int, duration_ms: int) -> float:
    if duration_ms <= 0:
        return 1.0
    return max(0.0, min(1.0, elapsed_ms / duration_ms))
