"""Real near-ultrasound chirp waveform generation + matched-filter ToF detection.

Phase 9 fills the acoustic half of the tier-2 seam that ranging.py's docstring
describes as "audio capture... behind an injectable seam" - this module owns
the actual DSP (waveform synthesis + detection), not the I/O (that is
real_ranging_source.py's job, via sounddevice).

Design: a linear chirp swept across 18-21kHz (near-ultrasound - audible to
almost no adult, inaudible to most, and a narrow enough band that consumer
speakers/mics still reproduce it reasonably). The receiver runs a matched
filter (cross-correlation against the known emitted chirp template) over the
captured samples; the correlation peak's sample offset is the time-of-flight.
This is the standard chirp/pulse-compression ranging technique - the same
principle behind sonar/radar chirp pulse compression - and requires no new
dependency beyond numpy/scipy, per this phase's TECH_STACK.md constraint.

Zero imports of sounddevice/asyncio here - this module is pure DSP over numpy
arrays, unit-testable with synthesized signals and a known injected delay,
mirroring ranging.py's own discipline of keeping pure math separate from I/O.
"""

from dataclasses import dataclass

import numpy as np
from scipy.signal import chirp as scipy_chirp
from scipy.signal import correlate

# Near-ultrasound sweep band. Chosen to sit above most adult hearing (<18kHz
# for most people) while staying within consumer speaker/mic frequency
# response (many roll off above ~20-22kHz), matching PRD's stated 18-21kHz.
CHIRP_START_HZ = 18_000.0
CHIRP_END_HZ = 21_000.0

# Chirp duration: long enough to give the matched filter a good correlation
# peak (more samples = more processing gain = better ToF resolution) but
# short enough to keep the tier-2 duty cycle low (one chirp per contest
# episode, per aggregator.py's _ranging_loop docstring).
CHIRP_DURATION_S = 0.010  # 10 ms

# Sample rate: must be well above 2x CHIRP_END_HZ (Nyquist) to avoid
# aliasing the sweep; 48kHz is a standard consumer audio device rate.
SAMPLE_RATE_HZ = 48_000

# A captured recording must correlate at least this strongly (normalized
# 0..1) against the emitted template for detect_chirp to report a detection
# at all. Below this, treat as "did not hear the chirp" - ranging.py's
# ChirpMeasurement absence-is-the-signal design (room-containment bit).
MIN_DETECTION_CORRELATION = 0.3


@dataclass(frozen=True)
class ChirpDetection:
    """Result of running the matched filter over a captured recording.

    `tof_us` is the one-way time-of-flight in microseconds from the start of
    the capture window to the correlation peak - callers convert this to an
    absolute measurement time using their own emit-time bookkeeping (see
    real_ranging_source.py). `correlation` is the peak's normalized
    correlation strength (0..1); `detected` is False when the peak did not
    clear MIN_DETECTION_CORRELATION (the chirp was not heard - a wall, out of
    beam, or pure noise).
    """

    detected: bool
    tof_us: float
    correlation: float


def generate_chirp(
    duration_s: float = CHIRP_DURATION_S,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
    start_hz: float = CHIRP_START_HZ,
    end_hz: float = CHIRP_END_HZ,
) -> np.ndarray:
    """Generate a linear chirp waveform swept from start_hz to end_hz.

    Returns a float64 numpy array in [-1, 1], `duration_s` seconds long at
    `sample_rate_hz`. Uses scipy.signal.chirp with a Hann window applied to
    taper the edges - an un-windowed chirp has sharp on/off transients that
    smear the matched-filter correlation peak and can clip real hardware.
    """
    n_samples = int(round(duration_s * sample_rate_hz))
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    waveform = scipy_chirp(t, f0=start_hz, f1=end_hz, t1=duration_s, method="linear")
    window = np.hanning(n_samples)
    return (waveform * window).astype(np.float64)


def detect_chirp(
    captured: np.ndarray,
    template: np.ndarray | None = None,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
) -> ChirpDetection:
    """Matched-filter (cross-correlation) time-of-flight detection.

    Cross-correlates `captured` against `template` (the known emitted chirp;
    defaults to generate_chirp()'s default waveform), finds the correlation
    peak, and converts the peak's sample offset into a one-way ToF in
    microseconds. Correlation is normalized by the template's own
    self-correlation energy so `correlation` is comparable across different
    capture amplitudes/gains.

    If `captured` is shorter than `template`, or the peak correlation does
    not clear MIN_DETECTION_CORRELATION, returns a not-detected result
    (tof_us=0.0) rather than raising - a quiet/absent capture is a normal,
    expected outcome (behind a wall / out of beam), not an error.
    """
    if template is None:
        template = generate_chirp(sample_rate_hz=sample_rate_hz)

    if captured.size < template.size:
        return ChirpDetection(detected=False, tof_us=0.0, correlation=0.0)

    correlation = correlate(captured, template, mode="valid")
    template_energy = np.sqrt(np.sum(template.astype(np.float64) ** 2))
    captured_energy = np.sqrt(np.sum(captured.astype(np.float64) ** 2))
    denom = template_energy * captured_energy
    normalized = correlation / denom if denom > 0 else np.zeros_like(correlation)

    peak_index = int(np.argmax(np.abs(normalized)))
    peak_correlation = float(np.abs(normalized[peak_index]))

    if peak_correlation < MIN_DETECTION_CORRELATION:
        return ChirpDetection(detected=False, tof_us=0.0, correlation=peak_correlation)

    tof_us = (peak_index / sample_rate_hz) * 1_000_000.0
    return ChirpDetection(detected=True, tof_us=tof_us, correlation=peak_correlation)
