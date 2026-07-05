"""Tests for the pure conversation FSM (conversation.py).

Run with: pytest tests/test_conversation.py -v

These tests treat the FSM as a deterministic function of (state, tick) and
never touch the network, edge_tts, or asyncio. The aggregator integration
is covered separately in test_aggregator.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conversation import (
    PHASE_CONFIRM,
    PHASE_IDLE,
    PHASE_PREPARE,
    PHASE_RELEASE,
    PHASE_TRANSFER,
    ConversationState,
    advance_phase,
    begin_handoff,
    finish_utterance,
    has_active_utterance,
    start_utterance,
    utterance_progress,
)

# All tests use a 50ms tick interval (matches the aggregator test harness).
TICK_MS = 50


def _speak(state, scanner="SIM-A", text="hello aether", tick=10, duration_ms=2000):
    """Helper: start a non-synthetic utterance with no real audio."""
    return start_utterance(
        state,
        scanner=scanner,
        text=text,
        audio_base64=None,
        duration_ms=duration_ms,
        is_synthetic=False,
        tick=tick,
        ts="12:00:00",
    )


# ---------------------------------------------------------------------------
# start_utterance
# ---------------------------------------------------------------------------

def test_start_utterance_sets_speaking_and_appends_transcript():
    state = ConversationState.empty()
    state = _speak(state, scanner="SIM-A", text="hi", tick=10)

    assert state.speaking_scanner == "SIM-A"
    assert has_active_utterance(state) is True
    assert state.utterance.text == "hi"
    assert state.utterance.started_at_tick == 10
    assert state.phase == PHASE_IDLE
    assert len(state.transcript) == 1
    assert state.transcript[0].text == "hi"
    assert state.transcript[0].scanner == "SIM-A"
    assert state.transcript[0].role == "assistant"


def test_start_utterance_increments_transcript_id():
    state = ConversationState.empty()
    state = _speak(state, text="first")
    state = _speak(state, text="second")
    ids = [e.id for e in state.transcript]
    assert ids == [1, 2]


def test_start_utterance_replaces_in_flight_utterance_and_cancels_handoff():
    # Start, begin a handoff, then a fresh say should reset to IDLE.
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=20)
    assert state.phase == PHASE_PREPARE

    state = _speak(state, scanner="SIM-A", text="restart", tick=30)
    assert state.phase == PHASE_IDLE
    assert state.speaking_scanner == "SIM-A"


# ---------------------------------------------------------------------------
# begin_handoff
# ---------------------------------------------------------------------------

def test_begin_handoff_noop_when_not_speaking():
    state = ConversationState.empty()
    new_state = begin_handoff(state, "SIM-A", "SIM-B", tick=10)
    assert new_state is state  # unchanged identity (early return)


def test_begin_handoff_noop_when_speaking_scanner_is_not_from_scanner():
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    # speaking_scanner is SIM-A; asking to hand off from SIM-B -> no-op.
    new_state = begin_handoff(state, "SIM-B", "SIM-A", tick=20)
    assert new_state is state


def test_begin_handoff_starts_prepare_when_speaking():
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=20)
    assert state.phase == PHASE_PREPARE
    assert state.phase_from == "SIM-A"
    assert state.phase_to == "SIM-B"
    assert state.phase_started_at_tick == 20
    # speaking_scanner hasn't flipped yet - that happens at CONFIRM.
    assert state.speaking_scanner == "SIM-A"
    # Utterance remains active and untouched.
    assert has_active_utterance(state) is True


def test_begin_handoff_noop_when_handoff_already_in_progress():
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=20)
    assert state.phase == PHASE_PREPARE
    # A second begin_handoff mid-FSM must not reset or stomp the first.
    new_state = begin_handoff(state, "SIM-A", "SIM-B", tick=25)
    assert new_state is state


# ---------------------------------------------------------------------------
# advance_phase - full cycle
# ---------------------------------------------------------------------------

def test_advance_phase_full_cycle_prepare_transfer_confirm_release_idle():
    """Drive the FSM through all four phases back to IDLE, one elapsed-phase
    per call. Each phase is 200ms; with a 50ms tick that is 4 ticks per phase."""
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=100)
    assert state.phase == PHASE_PREPARE

    # PREPARE started at tick 100; elapse 4 ticks (200ms) -> TRANSFER.
    state = advance_phase(state, now_tick=104, tick_ms=TICK_MS)
    assert state.phase == PHASE_TRANSFER
    assert state.speaking_scanner == "SIM-A"  # still old owner
    assert state.phase_started_at_tick == 104

    # TRANSFER -> CONFIRM (this is where speaking flips).
    state = advance_phase(state, now_tick=108, tick_ms=TICK_MS)
    assert state.phase == PHASE_CONFIRM
    assert state.speaking_scanner == "SIM-B"  # flipped to new owner

    # CONFIRM -> RELEASE.
    state = advance_phase(state, now_tick=112, tick_ms=TICK_MS)
    assert state.phase == PHASE_RELEASE
    assert state.speaking_scanner == "SIM-B"

    # RELEASE -> IDLE (handoff complete).
    state = advance_phase(state, now_tick=116, tick_ms=TICK_MS)
    assert state.phase == PHASE_IDLE
    assert state.phase_from is None
    assert state.phase_to is None
    # Utterance still active under the new owner.
    assert has_active_utterance(state) is True
    assert state.speaking_scanner == "SIM-B"


def test_advance_phase_does_not_advance_before_phase_elapsed():
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=100)
    # Only 2 ticks elapsed (100ms) - PREPARE needs 200ms. No transition.
    new_state = advance_phase(state, now_tick=102, tick_ms=TICK_MS)
    assert new_state.phase == PHASE_PREPARE
    assert new_state.phase_started_at_tick == 100  # unchanged


def test_advance_phase_idle_is_noop():
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    new_state = advance_phase(state, now_tick=999, tick_ms=TICK_MS)
    assert new_state is state


def test_release_completes_even_if_many_ticks_passed_in_release():
    """Skipping ahead many ticks in RELEASE still cleanly returns to IDLE
    (no overshoot, no re-trigger of the sequence)."""
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=100)
    # Advance to RELEASE.
    for now in (104, 108, 112):
        state = advance_phase(state, now_tick=now, tick_ms=TICK_MS)
    assert state.phase == PHASE_RELEASE
    # Skip far ahead - should land at IDLE, not loop.
    state = advance_phase(state, now_tick=9999, tick_ms=TICK_MS)
    assert state.phase == PHASE_IDLE
    assert state.speaking_scanner == "SIM-B"


# ---------------------------------------------------------------------------
# finish_utterance
# ---------------------------------------------------------------------------

def test_finish_utterance_clears_active_but_keeps_transcript():
    state = _speak(ConversationState.empty(), text="hello")
    assert has_active_utterance(state) is True

    state = finish_utterance(state)
    assert state.utterance is None
    assert state.speaking_scanner is None
    assert state.phase == PHASE_IDLE
    assert len(state.transcript) == 1
    assert state.transcript[0].text == "hello"


def test_finish_utterance_cancels_in_flight_handoff():
    state = _speak(ConversationState.empty(), scanner="SIM-A")
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=20)
    assert state.phase == PHASE_PREPARE

    state = finish_utterance(state)
    assert state.phase == PHASE_IDLE
    assert state.phase_from is None
    assert state.utterance is None


def test_finish_utterance_noop_when_nothing_active():
    state = ConversationState.empty()
    new_state = finish_utterance(state)
    assert new_state is state


# ---------------------------------------------------------------------------
# utterance_progress
# ---------------------------------------------------------------------------

def test_progress_starts_at_zero():
    state = _speak(ConversationState.empty(), tick=10, duration_ms=2000)
    # Same tick as start -> 0 progress.
    assert utterance_progress(state, now_tick=10, tick_ms=TICK_MS) == 0.0


def test_progress_advances_with_ticks():
    state = _speak(ConversationState.empty(), tick=10, duration_ms=2000)
    # 10 ticks * 50ms = 500ms elapsed of 2000ms -> 0.25
    assert utterance_progress(state, now_tick=20, tick_ms=TICK_MS) == 0.25


def test_progress_clamps_at_one():
    state = _speak(ConversationState.empty(), tick=10, duration_ms=2000)
    # Way past duration -> clamped to 1.0.
    assert utterance_progress(state, now_tick=1000, tick_ms=TICK_MS) == 1.0


def test_progress_is_zero_with_no_utterance():
    state = ConversationState.empty()
    assert utterance_progress(state, now_tick=999, tick_ms=TICK_MS) == 0.0


def test_progress_freezes_during_transfer():
    """During TRANSFER/CONFIRM the audio is paused; progress must freeze at
    the recorded offset rather than keep advancing on the wall clock."""
    state = _speak(ConversationState.empty(), tick=0, duration_ms=2000)
    # Walk into a handoff and advance to TRANSFER.
    state = begin_handoff(state, "SIM-A", "SIM-B", tick=20)
    state = advance_phase(state, now_tick=24, tick_ms=TICK_MS)
    assert state.phase == PHASE_TRANSFER

    # Now many ticks pass while paused - progress must not jump forward.
    progress_paused = utterance_progress(state, now_tick=24, tick_ms=TICK_MS)
    progress_later = utterance_progress(state, now_tick=100, tick_ms=TICK_MS)
    assert progress_paused == progress_later
