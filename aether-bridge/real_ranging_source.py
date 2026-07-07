"""Wires chirp_audio.py's emit/detect DSP into the exact RangingSource
callable signature aggregator.py's `_ranging_source` seam expects:
`(Contest, tick) -> ChirpResult | None`.

This module is the ONE piece of Phase 9 that cannot be verified end-to-end by
an agent without two physical speaker+mic devices in the same room (see
docs/phase9/ARCHITECTURE.md's "Key risk called out inline" and PRD.md's
acceptance criteria). What IS verified here (see tests/test_real_ranging_source.py):
the wiring is structurally correct - right signature, right ChirpResult
construction, sounddevice calls happen with the right arguments - using a
mocked/stubbed sounddevice. What is NOT verified: an actual acoustic
round-trip through real air, real hardware, real ambient noise. That is
manual-verification only, exactly like Phase 8's wake-word mic accuracy.

Physical model this module implements: this process has ONE speaker and ONE
microphone (the machine the aggregator runs on - PRD's "desktop + laptop"
test rig means one of those two machines IS the aggregator host). It emits
the chirp through its own speaker and listens on its own mic for the same
chirp reflected/received back. That single local capture stands in for
"did THIS scanner hear the chirp" - there is no cross-process audio
transport in this codebase (each remote scanner is a separate bridge.py/
simulated_scanner.py process with no chirp-capture protocol of its own), so
a full multi-machine acoustic mesh is out of scope for this phase, exactly as
ARCHITECTURE.md's "what is explicitly NOT built here" section implies by
only requiring the seam be filled, not a new inter-scanner audio protocol.
"""

import asyncio

import numpy as np

from chirp_audio import ChirpDetection, detect_chirp, generate_chirp
from ranging import ChirpMeasurement, ChirpResult, Contest, chirp_from_measurements

# How long to record after emitting the chirp, in seconds. Must comfortably
# exceed the max round-trip time we'd ever expect at in-room distances (a
# few meters => low tens of ms) plus the chirp's own duration.
CAPTURE_DURATION_S = 0.20

# Which scanner id this local speaker+mic pair represents when it hears its
# own chirp. Configurable at construction (see make_real_ranging_source)
# since either the incumbent or the challenger scanner could be the one
# co-located with the aggregator's audio hardware, depending on deployment.


def _record_and_detect(sample_rate_hz: int) -> ChirpDetection:
    """Emit one chirp via the default speaker, capture via the default mic,
    and run the matched filter over the capture. Blocking (sounddevice's
    play/rec calls are synchronous) - callers run this off the asyncio loop
    via asyncio.to_thread, matching wake_listener.py's convention of keeping
    sounddevice I/O off the event loop.
    """
    import sounddevice as sd

    template = generate_chirp(sample_rate_hz=sample_rate_hz)
    capture_samples = int(CAPTURE_DURATION_S * sample_rate_hz)

    recording = sd.rec(
        capture_samples,
        samplerate=sample_rate_hz,
        channels=1,
        dtype="float64",
    )
    sd.play(template, samplerate=sample_rate_hz)
    sd.wait()
    captured = np.asarray(recording).reshape(-1)
    return detect_chirp(captured, template=template, sample_rate_hz=sample_rate_hz)


def make_real_ranging_source(
    local_scanner_id: str,
    sample_rate_hz: int = 48_000,
):
    """Build a real RangingSource callable bound to this process's local
    speaker+mic, representing `local_scanner_id` in every contest.

    Returned callable matches `(Contest, tick) -> ChirpResult | None` exactly,
    the signature aggregator.py's `_ranging_source` seam requires (see
    aggregator.py:293 construction, aggregator.py:492 call site - both
    UNCHANGED by this phase). On each call:
      1. Emit one chirp through the local speaker, capture via the local mic.
      2. Run chirp_audio.detect_chirp's matched filter over the capture.
      3. If detected, produce a ChirpMeasurement for `local_scanner_id` at the
         measured distance; the OTHER contest party never gets a measurement
         from this local single-mic source (this process cannot hear what a
         remote scanner's mic heard - see module docstring's physical-model
         note) - that absence is consistent with ranging.py's own
         "absence IS the room-containment signal" design, it just means this
         phase's real source can only ever confirm/deny local presence, not
         adjudicate a full two-sided round-trip without a second mic feed.
      4. Hand the single measurement (or none) to ranging.chirp_from_measurements,
         which is UNCHANGED - identical fusion math for real or synthetic input.
    """

    def source(contest: Contest, tick: int) -> ChirpResult | None:
        detection = _record_and_detect(sample_rate_hz)
        if not detection.detected:
            return chirp_from_measurements((), contest, tick)

        from ranging import tof_to_distance

        measurement = ChirpMeasurement(
            scanner_id=local_scanner_id,
            tof_us=detection.tof_us,
            distance_m=tof_to_distance(detection.tof_us),
        )
        return chirp_from_measurements((measurement,), contest, tick)

    return source


async def real_ranging_source_async(
    contest: Contest,
    tick: int,
    local_scanner_id: str,
    sample_rate_hz: int = 48_000,
) -> ChirpResult | None:
    """Async-friendly variant: runs the blocking sounddevice emit/capture in
    a worker thread via asyncio.to_thread, so a caller already inside the
    aggregator's asyncio loop (see aggregator._ranging_loop) does not block
    the event loop for CAPTURE_DURATION_S per contest. Not used directly by
    `_ranging_source` (which is called synchronously in `_ranging_loop`) -
    provided for a future async-aware call site / manual testing script.
    """
    source = make_real_ranging_source(local_scanner_id, sample_rate_hz)
    return await asyncio.to_thread(source, contest, tick)
