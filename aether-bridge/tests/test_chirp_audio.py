"""Tests for chirp_audio.py's real DSP: chirp generation + matched-filter ToF.

These are real numpy/scipy signal-processing tests. A known delay is injected
by padding the emitted chirp template with silence (simulating acoustic
propagation time) plus additive Gaussian noise (simulating a real mic's
noise floor), then the matched filter is run against that synthesized
capture and the detected ToF is asserted against the KNOWN injected delay
within a stated tolerance. No hardware, no sounddevice - pure DSP math,
run for real, multiple trials, per this phase's verification mandate.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chirp_audio import (
    CHIRP_DURATION_S,
    SAMPLE_RATE_HZ,
    detect_chirp,
    generate_chirp,
)

# Tolerance for ToF detection: one sample period at SAMPLE_RATE_HZ is the
# theoretical minimum resolution of a matched filter without interpolation;
# we allow a small multiple of that to absorb noise-induced jitter in the
# correlation peak location. At 48kHz, one sample = ~20.8us.
SAMPLE_PERIOD_US = (1.0 / SAMPLE_RATE_HZ) * 1_000_000.0
TOF_TOLERANCE_US = SAMPLE_PERIOD_US * 3  # ~62.5us tolerance


def _build_capture(
    delay_samples: int,
    noise_amplitude: float = 0.0,
    trailing_silence_s: float = 0.02,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a synthetic capture: silence(delay) + chirp + silence(trailing) + noise.

    Returns (captured, template) so the caller can run detect_chirp against
    the exact template used to build the capture (mirrors how a real emitter
    and receiver share the same known chirp waveform).
    """
    rng = np.random.default_rng(seed)
    template = generate_chirp()
    lead_silence = np.zeros(delay_samples, dtype=np.float64)
    trail_silence = np.zeros(int(trailing_silence_s * SAMPLE_RATE_HZ), dtype=np.float64)
    signal = np.concatenate([lead_silence, template, trail_silence])
    if noise_amplitude > 0:
        signal = signal + rng.normal(0.0, noise_amplitude, size=signal.shape)
    return signal, template


def test_generate_chirp_shape_and_bounds() -> None:
    waveform = generate_chirp()
    expected_samples = int(round(CHIRP_DURATION_S * SAMPLE_RATE_HZ))
    assert waveform.shape == (expected_samples,)
    assert np.all(np.abs(waveform) <= 1.0 + 1e-9)


def test_generate_chirp_is_deterministic() -> None:
    a = generate_chirp()
    b = generate_chirp()
    assert np.allclose(a, b)


@pytest.mark.parametrize("delay_ms", [0.0, 1.0, 5.0, 10.0, 20.0])
def test_detected_tof_matches_injected_delay_no_noise(delay_ms: float) -> None:
    delay_samples = int(round((delay_ms / 1000.0) * SAMPLE_RATE_HZ))
    captured, template = _build_capture(delay_samples, noise_amplitude=0.0)

    result = detect_chirp(captured, template=template)

    injected_tof_us = (delay_samples / SAMPLE_RATE_HZ) * 1_000_000.0
    assert result.detected is True
    assert abs(result.tof_us - injected_tof_us) <= TOF_TOLERANCE_US, (
        f"delay_ms={delay_ms}: detected {result.tof_us}us vs injected {injected_tof_us}us"
    )


@pytest.mark.parametrize("trial_seed", range(10))
def test_detected_tof_matches_injected_delay_with_noise(trial_seed: int) -> None:
    # Realistic-ish: chirp amplitude is 1.0 peak (post-Hann-window, generally
    # lower), noise floor at 0.1 is a fairly noisy real mic capture.
    delay_ms = 7.5
    delay_samples = int(round((delay_ms / 1000.0) * SAMPLE_RATE_HZ))
    captured, template = _build_capture(
        delay_samples, noise_amplitude=0.1, seed=trial_seed
    )

    result = detect_chirp(captured, template=template)

    injected_tof_us = (delay_samples / SAMPLE_RATE_HZ) * 1_000_000.0
    assert result.detected is True
    assert abs(result.tof_us - injected_tof_us) <= TOF_TOLERANCE_US, (
        f"trial={trial_seed}: detected {result.tof_us}us vs injected {injected_tof_us}us "
        f"(correlation={result.correlation})"
    )


def test_pure_noise_is_not_detected() -> None:
    rng = np.random.default_rng(42)
    template = generate_chirp()
    pure_noise = rng.normal(0.0, 0.2, size=template.size * 3)

    result = detect_chirp(pure_noise, template=template)

    assert result.detected is False
    assert result.tof_us == 0.0


def test_too_short_capture_is_not_detected() -> None:
    template = generate_chirp()
    too_short = np.zeros(template.size // 2, dtype=np.float64)

    result = detect_chirp(too_short, template=template)

    assert result.detected is False


def test_detect_chirp_default_template_matches_generate_chirp() -> None:
    # Calling detect_chirp without an explicit template should use the same
    # default waveform generate_chirp() produces, so a real emitter/receiver
    # pair that both call the module defaults stay in sync.
    delay_samples = int(round(0.003 * SAMPLE_RATE_HZ))
    captured, _template = _build_capture(delay_samples, noise_amplitude=0.05, seed=1)

    result = detect_chirp(captured)

    injected_tof_us = (delay_samples / SAMPLE_RATE_HZ) * 1_000_000.0
    assert result.detected is True
    assert abs(result.tof_us - injected_tof_us) <= TOF_TOLERANCE_US
