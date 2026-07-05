"""Pure tier-2 ranging logic for the Aether Protocol.

Mirrors election.py and conversation.py's discipline: zero imports of
bleak/websockets/asyncio and no wall-clock reads, so this module is a
deterministic function of its inputs and can be unit-tested tick-by-tick
without any hardware or audio capture.

The model: BLE (tier 1) resolves ~80% of arbitrations alone via RSSI +
hysteresis (see election.py). The remaining cases - two devices within the
hysteresis margin, where RSSI fundamentally cannot tell them apart - are
"contested". A contested election escalates to tier 2: a near-ultrasound
chirp whose time-of-flight ranges to cm accuracy AND whose key physical
property is that it does not pass through walls, so hearing it proves
same-room presence - the one bit RSSI cannot provide (2m away in-room vs.
2m behind a wall look identical to BLE).

This module owns three deterministic pieces of that pipeline:
    1. detect_contest()    - is the current BLE election too close to call?
    2. chirp math          - tof -> distance, and picking a winner from a
                             set of per-device chirp measurements.
    3. fuse()              - given the BLE owner + chirp evidence, who wins?

It does NOT own audio capture (that is I/O and lives in the aggregator,
behind an injectable seam - see Aggregator._ranging_source). The fusion
logic here is identical whether the ChirpResult came from a real microphone
or a deterministic synthetic source.

Constants are pulled from election.py where they belong (HYSTERESIS_DB) so
there is a single source of truth for the election boundary.
"""

from dataclasses import dataclass

from election import HYSTERESIS_DB, ScannerState

# Speed of sound at ~20C in dry air at sea level. Used to convert a chirp's
# measured time-of-flight into a distance. Kept here (not in the aggregator)
# so the ToF math is unit-testable alongside the rest of the tier-2 logic.
SPEED_OF_SOUND_M_S = 343.0

# A contest exists when the strongest challenger's calibrated RSSI is within
# this many dB of the incumbent's, but has not yet exceeded HYSTERESIS_DB -
# i.e. BLE alone cannot confidently resolve ownership. Within this window the
# aggregator escalates to a tier-2 chirp to break the tie deterministically.
# The value is intentionally tighter than HYSTERESIS_DB so that only genuine
# photo-finishes (not every run-of-the-mill challenge) pay the chirp cost.
CONTEST_MARGIN_DB = 3.0


@dataclass(frozen=True)
class Contest:
    """A detected BLE photo-finish that tier 1 cannot resolve.

    Emitted by detect_contest() when the strongest challenger's calibrated
    RSSI is within CONTEST_MARGIN_DB of the incumbent but hasn't exceeded
    HYSTERESIS_DB. The aggregator responds by firing one tier-2 chirp (per
    contest episode - see Aggregator._ranging_loop) to break the tie using a
    signal RSSI cannot see.
    """

    incumbent_id: str
    challenger_id: str
    incumbent_rssi: float
    challenger_rssi: float
    at_tick: int


@dataclass(frozen=True)
class ChirpMeasurement:
    """One device's reception of a chirp: its time-of-flight and distance.

    A device that did NOT hear the chirp (behind a wall, out of beam, mic
    busy) simply has no measurement in the result - that absence IS the
    room-containment signal. `tof_us` is one-way (emit-time to receive-time)
    in microseconds; `distance_m` is tof converted via SPEED_OF_SOUND_M_S.
    """

    scanner_id: str
    tof_us: float
    distance_m: float


@dataclass(frozen=True)
class ChirpResult:
    """A complete chirp round: every measurement heard + the resolved winner.

    `measurements` is the set of devices that heard the chirp (absent devices
    are behind a wall / out of beam). `winner_id` is the closest device among
    those that heard it (min distance_m), or None if nothing was heard.
    `same_room` is True iff BOTH contest parties appear in measurements - the
    room-containment bit that settles ties RSSI cannot. `resolved_tick` is
    when the chirp round was produced.
    """

    measurements: tuple[ChirpMeasurement, ...]
    winner_id: str | None
    same_room: bool
    resolved_tick: int


@dataclass(frozen=True)
class FusionResult:
    """Outcome of fusing BLE ownership with chirp evidence.

    `owner` is the resolved owner after fusion. `overridden_by_ranging` is
    True when the chirp overrode the BLE-elected owner (the tier-2 value
    proposition firing). `reason` is a short machine-readable tag the
    aggregator/broadcast uses for diagnostics and (eventually) dashboard
    labelling - one of:
        "ble-only"             - no contest / no chirp; BLE result stands.
        "chirp-confirmed"      - chirp agreed with the BLE owner.
        "chirp-resolved-tie"   - chirp overrode BLE to break a tie.
        "chirp-room-containment" - chirp overrode BLE because the BLE winner
                                 did not hear the chirp (behind a wall).
    """

    owner: str | None
    overridden_by_ranging: bool
    reason: str


# ---------------------------------------------------------------------------
# Contest detection
# ---------------------------------------------------------------------------


def _candidates(scanners: list[ScannerState]) -> list[ScannerState]:
    return [s for s in scanners if s.present and s.smoothed_rssi is not None]


def _strongest(scanners: list[ScannerState]) -> ScannerState:
    """Same tie-break rule as election._strongest: louder wins, exact ties
    broken by lexically smaller id. Duplicated here (rather than imported)
    so this module's contest logic is self-contained and obviously pure."""
    return min(scanners, key=lambda s: (-s.calibrated_rssi(), s.id))


def detect_contest(
    owner: str | None,
    scanners: list[ScannerState],
    tick: int,
) -> Contest | None:
    """Is the current BLE election too close to call?

    Returns a Contest when there is a present incumbent AND a present
    challenger whose calibrated RSSI is within CONTEST_MARGIN_DB of the
    incumbent but has not exceeded HYSTERESIS_DB. Returns None otherwise
    (no owner, no challenger, a runaway win, or a clear hysteresis-grade
    challenge that tier 1 resolves on its own).
    """
    candidates = _candidates(scanners)
    if owner is None:
        return None
    incumbent = next((s for s in candidates if s.id == owner), None)
    if incumbent is None:
        # Owner not currently present (or not a candidate) - tier 1 will
        # reacquire; nothing for tier 2 to contest.
        return None
    others = [s for s in candidates if s.id != owner]
    if not others:
        return None

    challenger = _strongest(others)
    incumbent_rssi = incumbent.calibrated_rssi()
    challenger_rssi = challenger.calibrated_rssi()
    # gap > 0 means the challenger reads louder (stronger) than the incumbent.
    gap = challenger_rssi - incumbent_rssi

    # Contested zone: challenger is close enough that RSSI can't confidently
    # call it (within CONTEST_MARGIN_DB), but hasn't broken hysteresis. The
    # lower bound also catches a marginally-weaker challenger - a scanner
    # reading ~equal to the incumbent is just as undecided as one reading
    # marginally louder.
    if -CONTEST_MARGIN_DB <= gap < HYSTERESIS_DB:
        return Contest(
            incumbent_id=incumbent.id,
            challenger_id=challenger.id,
            incumbent_rssi=incumbent_rssi,
            challenger_rssi=challenger_rssi,
            at_tick=tick,
        )
    return None


# ---------------------------------------------------------------------------
# Chirp math
# ---------------------------------------------------------------------------


def tof_to_distance(tof_us: float, speed_of_sound_m_s: float = SPEED_OF_SOUND_M_S) -> float:
    """Convert a one-way chirp time-of-flight (microseconds) to distance (meters).

    distance = tof_seconds * speed_of_sound. One-way ToF assumes synchronized
    clocks between emitter and receiver (the coded-chirp pattern lets the
    receiver identify the emit time from the code, not a sync handshake).
    """
    return (tof_us / 1_000_000.0) * speed_of_sound_m_s


def chirp_from_measurements(
    measurements: tuple[ChirpMeasurement, ...],
    contest: Contest,
    tick: int,
) -> ChirpResult:
    """Assemble a ChirpResult from raw per-device measurements.

    Picks the winner as the measurement with the smallest distance_m (closest
    device wins - the whole point of cm-resolution ToF). Computes same_room as
    whether BOTH contest parties produced a measurement; if only one (or
    neither) heard the chirp, that absence is the room-containment signal.

    Ties in distance_m are broken by lexically smaller scanner id, matching
    election's deterministic tie-break so the two tiers never disagree on
    tie-break policy.
    """
    if not measurements:
        return ChirpResult(
            measurements=(),
            winner_id=None,
            same_room=False,
            resolved_tick=tick,
        )

    winner = min(measurements, key=lambda m: (m.distance_m, m.scanner_id))
    heard_ids = {m.scanner_id for m in measurements}
    same_room = contest.incumbent_id in heard_ids and contest.challenger_id in heard_ids
    return ChirpResult(
        measurements=measurements,
        winner_id=winner.scanner_id,
        same_room=same_room,
        resolved_tick=tick,
    )


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def fuse(
    owner: str | None,
    scanners: list[ScannerState],
    contest: Contest | None,
    chirp: ChirpResult | None,
) -> FusionResult:
    """Decide the resolved owner given BLE ownership + chirp evidence.

    Precedence (the tiered-sensing contract):
      - No contest or no chirp  -> BLE owner stands ("ble-only").
      - Chirp winner is the BLE owner -> stands, now chirp-confirmed.
      - Chirp winner is the OTHER contest party and is currently present ->
        override: the chirp broke the tie ("chirp-resolved-tie", or
        "chirp-room-containment" if the BLE winner did not hear the chirp).
      - Chirp winner is absent / unknown -> BLE owner stands (tier 2 produced
        no usable override; let tier 1 keep running).

    `scanners` is the same candidate list the election ran on, used only to
    confirm the chirp winner is still present before overriding.
    """
    if contest is None or chirp is None or chirp.winner_id is None:
        return FusionResult(owner=owner, overridden_by_ranging=False, reason="ble-only")

    present_ids = {s.id for s in _candidates(scanners)}
    if chirp.winner_id == owner:
        return FusionResult(owner=owner, overridden_by_ranging=False, reason="chirp-confirmed")

    # The chirp picked someone other than the BLE owner. Only honour it if
    # that scanner is currently a present candidate - tier 2 must never hand
    # ownership to a device tier 1 considers absent.
    if chirp.winner_id in present_ids:
        heard_ids = {m.scanner_id for m in chirp.measurements}
        reason = (
            "chirp-room-containment"
            if owner is not None and owner not in heard_ids
            else "chirp-resolved-tie"
        )
        return FusionResult(owner=chirp.winner_id, overridden_by_ranging=True, reason=reason)

    return FusionResult(owner=owner, overridden_by_ranging=False, reason="ble-only")
