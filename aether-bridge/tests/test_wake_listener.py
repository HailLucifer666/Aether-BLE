"""Unit tests for wake_listener.py's pure debounce logic.

should_send_wake takes plain floats (fake monotonic timestamps) and has zero
mic/model/websocket dependencies, so this file needs no hardware, no
openwakeword model load, and no network - pure logic, mirroring
tests/test_election.py's discipline.

Run with: pytest tests/test_wake_listener.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wake_listener import DEFAULT_DEBOUNCE_SECONDS, should_send_wake


def test_first_wake_always_sends():
    """No prior send (`last_sent_monotonic=None`) -> always allowed."""
    assert should_send_wake(None, now_monotonic=100.0) is True


def test_wake_within_debounce_window_is_suppressed():
    """A second wake arriving before the debounce window elapses is blocked."""
    last_sent = 100.0
    now = 100.5  # 0.5s later, under the 1.5s default debounce
    assert should_send_wake(last_sent, now) is False


def test_wake_exactly_at_debounce_boundary_is_allowed():
    """Exactly `debounce_seconds` elapsed counts as allowed (inclusive boundary)."""
    last_sent = 100.0
    now = last_sent + DEFAULT_DEBOUNCE_SECONDS
    assert should_send_wake(last_sent, now) is True


def test_wake_after_debounce_window_is_allowed():
    last_sent = 100.0
    now = 100.0 + DEFAULT_DEBOUNCE_SECONDS + 0.01
    assert should_send_wake(last_sent, now) is True


def test_custom_debounce_seconds_is_respected():
    last_sent = 50.0
    assert should_send_wake(last_sent, 50.2, debounce_seconds=0.5) is False
    assert should_send_wake(last_sent, 50.6, debounce_seconds=0.5) is True


def test_repeated_rapid_scores_only_first_send_allowed():
    """Simulates many near-simultaneous frames scoring above threshold while
    a single wake word is spoken; only the first should be allowed to send
    within the debounce window."""
    last_sent = None
    sends = []
    fake_now_values = [10.0, 10.05, 10.1, 10.2, 10.4, 10.9, 11.6]  # seconds
    for now in fake_now_values:
        if should_send_wake(last_sent, now):
            sends.append(now)
            last_sent = now
    # Only the first frame (10.0) and the one past the 1.5s window (11.6,
    # since 11.6 - 10.0 = 1.6s >= 1.5s) should have been allowed.
    assert sends == [10.0, 11.6]
