"""Tests for real_ranging_source.py - INTERFACE-ONLY verification.

IMPORTANT (per PRD.md/ARCHITECTURE.md's disclosed hardware limitation): these
tests mock/stub `sounddevice` entirely. They prove the wiring is structurally
correct - right (Contest, tick) -> ChirpResult signature, right ChirpResult
construction from a detected/undetected chirp_audio result, sounddevice
called with sane arguments. They do NOT and CANNOT prove a real acoustic
round-trip works (real speaker, real air, real mic, real ambient noise) -
that requires two physical speaker+mic devices in the same room and is
explicitly out of scope for agent verification this phase.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chirp_audio import generate_chirp
from ranging import Contest
from real_ranging_source import make_real_ranging_source


def _contest(incumbent="A", challenger="B", tick=1) -> Contest:
    return Contest(
        incumbent_id=incumbent,
        challenger_id=challenger,
        incumbent_rssi=-60.0,
        challenger_rssi=-58.0,
        at_tick=tick,
    )


def _fake_sounddevice_module(captured_signal: np.ndarray):
    """Build a MagicMock standing in for the `sounddevice` module, whose
    `rec()` returns a fixed captured_signal (as if that's what the mic
    "captured") and whose `play()`/`wait()` are no-ops. This is the mock
    boundary: real_ranging_source.py imports sounddevice lazily inside the
    function, so patching sys.modules['sounddevice'] intercepts it there.
    """
    fake_sd = MagicMock()
    fake_sd.rec.return_value = captured_signal.reshape(-1, 1)
    fake_sd.play.return_value = None
    fake_sd.wait.return_value = None
    return fake_sd


def test_source_has_correct_signature() -> None:
    source = make_real_ranging_source(local_scanner_id="A")
    assert callable(source)
    # Signature check: must accept exactly (Contest, tick) positionally,
    # matching aggregator.py's `self._ranging_source(contest, self._tick)`
    # call site (aggregator.py:492, UNCHANGED).
    import inspect

    sig = inspect.signature(source)
    params = list(sig.parameters.values())
    assert len(params) == 2


def test_detected_chirp_produces_chirp_result_with_local_measurement() -> None:
    sample_rate_hz = 48_000
    template = generate_chirp(sample_rate_hz=sample_rate_hz)
    # A capture that IS the template with no delay -> should detect at ~0 ToF.
    trailing_silence = np.zeros(int(0.05 * sample_rate_hz))
    captured_signal = np.concatenate([template, trailing_silence])

    fake_sd = _fake_sounddevice_module(captured_signal)
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        source = make_real_ranging_source(local_scanner_id="A", sample_rate_hz=sample_rate_hz)
        contest = _contest()
        result = source(contest, tick=5)

    assert result is not None
    assert result.resolved_tick == 5
    assert len(result.measurements) == 1
    assert result.measurements[0].scanner_id == "A"
    assert result.winner_id == "A"
    # sounddevice.play/rec/wait were actually invoked (the wiring called
    # into the audio I/O layer, even though it's mocked here).
    fake_sd.rec.assert_called_once()
    fake_sd.play.assert_called_once()
    fake_sd.wait.assert_called_once()


def test_undetected_chirp_produces_empty_chirp_result() -> None:
    sample_rate_hz = 48_000
    rng = np.random.default_rng(7)
    # Pure noise capture - matched filter should not clear the detection
    # threshold, simulating "did not hear the chirp" (behind a wall).
    pure_noise = rng.normal(0.0, 0.05, size=int(0.25 * sample_rate_hz))

    fake_sd = _fake_sounddevice_module(pure_noise)
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        source = make_real_ranging_source(local_scanner_id="A", sample_rate_hz=sample_rate_hz)
        contest = _contest()
        result = source(contest, tick=9)

    assert result is not None
    assert result.measurements == ()
    assert result.winner_id is None
    assert result.same_room is False
    assert result.resolved_tick == 9


def test_rec_called_with_correct_sample_rate_and_channels() -> None:
    sample_rate_hz = 44_100
    template = generate_chirp(sample_rate_hz=sample_rate_hz)
    captured_signal = np.concatenate([template, np.zeros(1000)])

    fake_sd = _fake_sounddevice_module(captured_signal)
    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        source = make_real_ranging_source(local_scanner_id="B", sample_rate_hz=sample_rate_hz)
        source(_contest(), tick=1)

    _args, kwargs = fake_sd.rec.call_args
    assert kwargs["samplerate"] == sample_rate_hz
    assert kwargs["channels"] == 1

    _play_args, play_kwargs = fake_sd.play.call_args
    assert play_kwargs["samplerate"] == sample_rate_hz


def test_returns_chirp_result_type_matching_ranging_module() -> None:
    # The result must be exactly ranging.ChirpResult (the dataclass
    # chirp_from_measurements produces) - proving this module never
    # constructs its own competing result type.
    from ranging import ChirpResult

    sample_rate_hz = 48_000
    template = generate_chirp(sample_rate_hz=sample_rate_hz)
    captured_signal = np.concatenate([template, np.zeros(1000)])
    fake_sd = _fake_sounddevice_module(captured_signal)

    with patch.dict(sys.modules, {"sounddevice": fake_sd}):
        source = make_real_ranging_source(local_scanner_id="A", sample_rate_hz=sample_rate_hz)
        result = source(_contest(), tick=1)

    assert isinstance(result, ChirpResult)
