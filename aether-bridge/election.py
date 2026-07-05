"""Pure leader-election logic for the multi-scanner BLE mesh.

Zero imports of bleak/websockets/asyncio and no wall-clock reads - this
module is a deterministic function of its inputs so it can be tested without
any I/O and driven tick-by-tick from real or simulated scanner data alike.

Election rule summary:
    - Only `present` scanners with a non-None smoothed RSSI are candidates.
    - No candidates => owner is None.
    - No current owner (first contact, or owner just went absent/lost) =>
      the strongest candidate wins immediately (re-acquisition).
    - Otherwise, a challenger must beat the incumbent's smoothedRssi by at
      least HYSTERESIS_DB for HYSTERESIS_CONSECUTIVE consecutive elect()
      calls before a handoff occurs. The challenger streak resets whenever
      the incumbent retakes the lead or a different scanner becomes the
      strongest challenger.
    - Exact ties in smoothedRssi are broken by lexically smaller scanner id.
"""

from dataclasses import dataclass, replace

HYSTERESIS_DB = 5.0
HYSTERESIS_CONSECUTIVE = 2


@dataclass(frozen=True)
class ScannerState:
    """A single scanner's latest observation, as seen by the aggregator.

    `calibration_offset` is an optional additive correction (in dB) applied
    to `smoothed_rssi` before any comparison, so that scanners with different
    radio/antenna sensitivity can be brought onto a common scale. Defaults to
    0.0 (no correction) and must be set explicitly per-scanner by whatever
    configures the mesh.
    """

    id: str
    smoothed_rssi: float | None
    present: bool
    calibration_offset: float = 0.0

    def calibrated_rssi(self) -> float | None:
        if self.smoothed_rssi is None:
            return None
        return self.smoothed_rssi + self.calibration_offset


@dataclass(frozen=True)
class ChallengerState:
    """Tracks the current challenger's consecutive-tick streak against the incumbent."""

    challenger_id: str | None = None
    consecutive_ticks: int = 0


@dataclass(frozen=True)
class Handoff:
    """Record of an ownership transfer that occurred on this elect() call."""

    from_id: str | None
    to_id: str


@dataclass(frozen=True)
class ElectResult:
    """Outcome of a single elect() call."""

    new_owner: str | None
    challenger: ChallengerState
    handoff: Handoff | None


def _is_candidate(scanner: ScannerState) -> bool:
    return scanner.present and scanner.smoothed_rssi is not None


def _strongest(candidates: list[ScannerState]) -> ScannerState:
    """Pick the strongest candidate; exact ties broken by lexically smaller id."""
    return min(candidates, key=lambda s: (-s.calibrated_rssi(), s.id))


def elect(
    current_owner: str | None,
    scanners: list[ScannerState],
    challenger: ChallengerState,
) -> ElectResult:
    """Run one election tick and return the resulting owner/challenger/handoff state."""
    candidates = [s for s in scanners if _is_candidate(s)]

    if not candidates:
        return ElectResult(new_owner=None, challenger=ChallengerState(), handoff=None)

    incumbent = next((s for s in candidates if s.id == current_owner), None)

    if incumbent is None:
        # First contact, or the previous owner is now absent/lost: the
        # strongest available candidate wins immediately.
        winner = _strongest(candidates)
        handoff = None
        if current_owner != winner.id:
            handoff = Handoff(from_id=current_owner, to_id=winner.id)
        return ElectResult(new_owner=winner.id, challenger=ChallengerState(), handoff=handoff)

    others = [s for s in candidates if s.id != incumbent.id]
    if not others:
        return ElectResult(new_owner=incumbent.id, challenger=ChallengerState(), handoff=None)

    strongest_other = _strongest(others)
    incumbent_rssi = incumbent.calibrated_rssi()
    challenger_rssi = strongest_other.calibrated_rssi()

    exceeds_hysteresis = (
        challenger_rssi is not None
        and incumbent_rssi is not None
        and challenger_rssi - incumbent_rssi >= HYSTERESIS_DB
    )

    if not exceeds_hysteresis:
        # Incumbent retook (or held) the lead: any in-progress challenge resets.
        return ElectResult(new_owner=incumbent.id, challenger=ChallengerState(), handoff=None)

    if challenger.challenger_id == strongest_other.id:
        streak = challenger.consecutive_ticks + 1
    else:
        # A different scanner is now the strongest challenger: streak resets.
        streak = 1

    if streak >= HYSTERESIS_CONSECUTIVE:
        handoff = Handoff(from_id=incumbent.id, to_id=strongest_other.id)
        return ElectResult(new_owner=strongest_other.id, challenger=ChallengerState(), handoff=handoff)

    new_challenger = replace(challenger, challenger_id=strongest_other.id, consecutive_ticks=streak)
    return ElectResult(new_owner=incumbent.id, challenger=new_challenger, handoff=None)
