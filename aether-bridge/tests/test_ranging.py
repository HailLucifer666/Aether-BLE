"""Tests for the pure tier-2 ranging module (ranging.py).

Run with: pytest tests/test_ranging.py -v

These tests treat ranging as a deterministic function of (BLE state, chirp
evidence) and never touch the network, audio capture, or asyncio. The
aggregator's ranging-source integration (the seam where a real microphone
plugs in) is covered separately in test_aggregator.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from election import HYSTERESIS_DB, ScannerState
from ranging import (
    CONTEST_MARGIN_DB,
    SPEED_OF_SOUND_M_S,
    ChirpMeasurement,
    ChirpResult,
    Contest,
    chirp_from_measurements,
    detect_contest,
    fuse,
    tof_to_distance,
)


def _scanner(id_: str, rssi: float | None, present: bool = True, offset: float = 0.0) -> ScannerState:
    return ScannerState(id=id_, smoothed_rssi=rssi, present=present, calibration_offset=offset)


def _chirp(scanner_id: str, tof_us: float) -> ChirpMeasurement:
    return ChirpMeasurement(
        scanner_id=scanner_id,
        tof_us=tof_us,
        distance_m=tof_to_distance(tof_us),
    )


def _result(winner_id: str | None, heard: tuple[ChirpMeasurement, ...], contest: Contest, tick: int) -> ChirpResult:
    return chirp_from_measurements(heard, contest, tick)


# ---------------------------------------------------------------------------
# detect_contest
# ---------------------------------------------------------------------------

def test_no_owner_is_never_contested() -> None:
    scanners = [_scanner("A", -60.0), _scanner("B", -59.0)]
    assert detect_contest(None, scanners, tick=10) is None


def test_single_scanner_is_never_contested() -> None:
    scanners = [_scanner("A", -60.0)]
    assert detect_contest("A", scanners, tick=10) is None


def test_owner_absent_is_never_contested() -> None:
    # Owner A is gone (absent); tier 1 will reacquire. Nothing to contest.
    scanners = [_scanner("A", None, present=False), _scanner("B", -60.0)]
    assert detect_contest("A", scanners, tick=10) is None


def test_challenger_clearly_weaker_is_not_contested() -> None:
    # B is 10 dB quieter -> not within CONTEST_MARGIN_DB.
    scanners = [_scanner("A", -60.0), _scanner("B", -70.0)]
    assert detect_contest("A", scanners, tick=10) is None


def test_challenger_clearly_louder_breaks_hysteresis_not_a_contest() -> None:
    # B exceeds HYSTERESIS_DB - tier 1 handles this on its own, no chirp needed.
    scanners = [_scanner("A", -60.0), _scanner("B", -60.0 + HYSTERESIS_DB + 0.1)]
    assert detect_contest("A", scanners, tick=10) is None


def test_challenger_within_margin_is_contested() -> None:
    scanners = [_scanner("A", -60.0), _scanner("B", -58.0)]  # B 2 dB louder, within margin
    contest = detect_contest("A", scanners, tick=42)
    assert contest is not None
    assert contest.incumbent_id == "A"
    assert contest.challenger_id == "B"
    assert contest.incumbent_rssi == -60.0
    assert contest.challenger_rssi == -58.0
    assert contest.at_tick == 42


def test_marginally_weaker_challenger_is_still_contested() -> None:
    # A scanner reading ~2 dB quieter than the incumbent is just as
    # undecided as one reading 2 dB louder - both sit inside the contest
    # window. This is the symmetric tie zone.
    scanners = [_scanner("A", -60.0), _scanner("B", -62.0)]
    contest = detect_contest("A", scanners, tick=1)
    assert contest is not None
    assert contest.challenger_id == "B"


def test_contest_uses_calibrated_rssi() -> None:
    # Same physical scenario as the election kill-test: B over-reports by
    # CONTEST_MARGIN_DB. Without offset, B appears to be in the contest zone;
    # with the offset cancelling the bias, the gap reopens and no contest.
    scanners_uncorrected = [_scanner("A", -60.0), _scanner("B", -57.0)]
    assert detect_contest("A", scanners_uncorrected, tick=1) is not None

    scanners_corrected = [_scanner("A", -60.0), _scanner("B", -57.0, offset=-3.0)]
    # B calibrated to -60 -> gap is 0, still within margin on the low side,
    # so still contested. Use a larger offset to push B clearly weaker.
    scanners_corrected = [_scanner("A", -60.0), _scanner("B", -57.0, offset=-10.0)]
    assert detect_contest("A", scanners_corrected, tick=1) is None


def test_contest_picks_strongest_challenger_by_calibrated_rssi() -> None:
    # A owns, B and C both present; C is the loudest challenger -> contested
    # against C, not B, even though B sits in the margin too.
    scanners = [
        _scanner("A", -60.0),
        _scanner("B", -58.5),
        _scanner("C", -58.0),
    ]
    contest = detect_contest("A", scanners, tick=1)
    assert contest is not None
    assert contest.challenger_id == "C"


def test_contest_tie_break_is_lexical_among_equal_challengers() -> None:
    # Two challengers reading identically - lexically smaller is the
    # detected challenger, mirroring election's tie-break policy.
    scanners = [_scanner("A", -60.0), _scanner("Z", -58.0), _scanner("B", -58.0)]
    contest = detect_contest("A", scanners, tick=1)
    assert contest is not None
    assert contest.challenger_id == "B"


# ---------------------------------------------------------------------------
# tof_to_distance / chirp_from_measurements
# ---------------------------------------------------------------------------

def test_tof_to_distance_round_trip() -> None:
    # 1 ms one-way ToF at 343 m/s = 0.343 m = 34.3 cm.
    assert tof_to_distance(1000.0) == 0.343
    assert tof_to_distance(0.0) == 0.0


def test_tof_to_distance_uses_constant_for_cm_accuracy() -> None:
    # 100 us one-way = 3.43 cm - the cm-resolution tier-2 promise.
    assert round(tof_to_distance(100.0) * 100, 1) == 3.4  # 3.43 cm


def test_chirp_from_measurements_picks_closest() -> None:
    contest = Contest(
        incumbent_id="A", challenger_id="B",
        incumbent_rssi=-60.0, challenger_rssi=-58.0, at_tick=5,
    )
    heard = (
        _chirp("A", 2000.0),  # 0.686 m
        _chirp("B", 1000.0),  # 0.343 m - closer
    )
    result = _result("B", heard, contest, tick=6)
    assert result.winner_id == "B"
    assert result.same_room is True
    assert result.resolved_tick == 6


def test_chirp_empty_measurements_no_winner() -> None:
    contest = Contest(
        incumbent_id="A", challenger_id="B",
        incumbent_rssi=-60.0, challenger_rssi=-58.0, at_tick=5,
    )
    result = _result("B", (), contest, tick=6)
    assert result.winner_id is None
    assert result.same_room is False


def test_chirp_one_party_absent_is_room_containment_signal() -> None:
    # The challenger heard the chirp; the incumbent did not. That asymmetry
    # is the room-containment bit: the incumbent is (say) behind a wall.
    contest = Contest(
        incumbent_id="A", challenger_id="B",
        incumbent_rssi=-60.0, challenger_rssi=-58.0, at_tick=5,
    )
    heard = (_chirp("B", 1000.0),)
    result = _result("B", heard, contest, tick=6)
    assert result.winner_id == "B"
    # A's absence means they are NOT in the same room.
    assert result.same_room is False


def test_chirp_distance_tie_broken_lexically() -> None:
    contest = Contest(
        incumbent_id="A", challenger_id="B",
        incumbent_rssi=-60.0, challenger_rssi=-60.0, at_tick=5,
    )
    heard = (_chirp("Z", 1000.0), _chirp("B", 1000.0))
    result = _result("Z", heard, contest, tick=6)
    assert result.winner_id == "B"  # lexically smaller wins the distance tie


# ---------------------------------------------------------------------------
# fuse
# ---------------------------------------------------------------------------

def test_fuse_no_contest_returns_ble_owner() -> None:
    scanners = [_scanner("A", -60.0), _scanner("B", -80.0)]
    result = fuse("A", scanners, contest=None, chirp=None)
    assert result.owner == "A"
    assert result.overridden_by_ranging is False
    assert result.reason == "ble-only"


def test_fuse_contest_but_no_chirp_keeps_ble_owner() -> None:
    scanners = [_scanner("A", -60.0), _scanner("B", -58.0)]
    contest = detect_contest("A", scanners, tick=1)
    assert contest is not None
    result = fuse("A", scanners, contest=contest, chirp=None)
    assert result.owner == "A"
    assert result.reason == "ble-only"


def test_fuse_chirp_confirms_ble_owner() -> None:
    scanners = [_scanner("A", -60.0), _scanner("B", -58.0)]
    contest = detect_contest("A", scanners, tick=1)
    chirp = ChirpResult(
        measurements=(_chirp("A", 1000.0), _chirp("B", 2000.0)),
        winner_id="A",  # chirp agrees with BLE
        same_room=True,
        resolved_tick=2,
    )
    result = fuse("A", scanners, contest, chirp)
    assert result.owner == "A"
    assert result.overridden_by_ranging is False
    assert result.reason == "chirp-confirmed"


def test_fuse_chirp_overrides_ble_to_break_tie() -> None:
    scanners = [_scanner("A", -60.0), _scanner("B", -58.0)]
    contest = detect_contest("A", scanners, tick=1)
    assert contest is not None
    chirp = ChirpResult(
        measurements=(_chirp("A", 2000.0), _chirp("B", 1000.0)),  # B closer
        winner_id="B",
        same_room=True,
        resolved_tick=2,
    )
    result = fuse("A", scanners, contest, chirp)
    assert result.owner == "B"
    assert result.overridden_by_ranging is True
    assert result.reason == "chirp-resolved-tie"


def test_fuse_chirp_room_containment_when_ble_winner_did_not_hear() -> None:
    # BLE says A owns; chirp picks B; A did not appear in measurements
    # (behind a wall). This is the strongest tier-2 case: a deterministic
    # override driven by the room-containment bit RSSI cannot see.
    scanners = [_scanner("A", -60.0), _scanner("B", -58.0)]
    contest = detect_contest("A", scanners, tick=1)
    chirp = ChirpResult(
        measurements=(_chirp("B", 1000.0),),  # only B heard it
        winner_id="B",
        same_room=False,
        resolved_tick=2,
    )
    result = fuse("A", scanners, contest, chirp)
    assert result.owner == "B"
    assert result.overridden_by_ranging is True
    assert result.reason == "chirp-room-containment"


def test_fuse_chirp_winner_must_be_present_to_override() -> None:
    # Chirp picks C, but C is now absent (mic busy / device gone). The
    # override must NOT hand ownership to an absent device - tier 1 keeps it.
    scanners = [
        _scanner("A", -60.0),
        _scanner("B", -58.0),
        _scanner("C", -90.0, present=False),  # absent
    ]
    contest = detect_contest("A", scanners, tick=1)
    chirp = ChirpResult(
        measurements=(_chirp("C", 100.0),),
        winner_id="C",
        same_room=False,
        resolved_tick=2,
    )
    result = fuse("A", scanners, contest, chirp)
    assert result.owner == "A"
    assert result.overridden_by_ranging is False
    assert result.reason == "ble-only"


# ---------------------------------------------------------------------------
# THE CROSS-TIER KILL-TEST
#
# Mirrors the philosophy of the election kill-test: a scenario where tier 1
# (BLE RSSI) is FUNDAMENTALLY UNABLE to make the right call, and tier 2
# (chirp ToF + room containment) resolves it deterministically. This is the
# whole reason tier 2 exists.
#
# Setup: a phone sits 2m from scanner A in the SAME ROOM, and ~2m from
# scanner B behind a WALL. The wall attenuates BLE by only a couple of dB
# (2.4 GHz penetrates drywall easily), so B reads MARGINALLY LOUDER than A
# (-62.0 vs -62.5 - B's reading happens to win by 0.5 dB, well inside the
# contest margin and far below hysteresis). Tier 1 therefore picks B - the
# WRONG device - and has no way to know the wall is there. The chirp settles
# it: A hears the chirp (ultrasound doesn't pass through the wall, so B
# cannot). fuse() must override B and hand ownership to A every single time,
# deterministically. This is the one bit RSSI fundamentally cannot provide.
# ---------------------------------------------------------------------------

def test_kill_test_wall_partition_ble_cannot_resolve_chirp_can() -> None:
    # B reads 0.5 dB louder -> BLE elects B (the wall-side scanner). The 0.5
    # dB gap is squarely inside the contest zone, so tier 2 fires.
    scanners = [_scanner("A", -62.5), _scanner("B", -62.0)]
    contest = detect_contest("B", scanners, tick=1)
    assert contest is not None, "a sub-margin RSSI gap must escalate to tier 2"
    assert contest.incumbent_id == "B"  # BLE's (wrong) pick

    # The wall: only A hears the chirp. B's absence from measurements IS the
    # room-containment bit. A's ToF (~11.7 ms one-way ~ 4 m round-trip-equivalent)
    # is irrelevant to the decision - same_room=False is what settles it.
    chirp = ChirpResult(
        measurements=(_chirp("A", 11_662.0),),
        winner_id="A",
        same_room=False,
        resolved_tick=2,
    )
    result = fuse("B", scanners, contest, chirp)

    assert result.owner == "A"
    assert result.overridden_by_ranging is True
    assert result.reason == "chirp-room-containment"
    # The crux: tier 2 produced the one bit tier 1 cannot, and the fusion
    # rule honored it. Repeat the same scenario 100x - it must be deterministic.
    for _ in range(100):
        assert fuse("B", scanners, contest, chirp).owner == "A"


# Sanity: the constant the module promises.
def test_speed_of_sound_constant_documented() -> None:
    assert SPEED_OF_SOUND_M_S == 343.0
    assert CONTEST_MARGIN_DB < HYSTERESIS_DB, (
        "the contest window must be tighter than the hysteresis window so "
        "only genuine photo-finishes escalate to a chirp, not every run-of-"
        "the-mill hysteresis-grade challenge"
    )
