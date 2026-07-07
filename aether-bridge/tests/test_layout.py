"""Tests for layout.py's persisted scanner placement + calibration store.

Persistence round-trip (set then get returns what was set) and bounds
validation (invalid/out-of-bounds input raises rather than corrupting the
file) - real pytest run against a temp JSON file, no mocking needed since
this module has no external I/O beyond that file.

Run with: pytest tests/test_layout.py -v
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from layout import (
    MAX_COORDINATE_METERS,
    MAX_PATH_LOSS_EXPONENT,
    MAX_RSSI_AT_1M,
    MIN_PATH_LOSS_EXPONENT,
    MIN_RSSI_AT_1M,
    LayoutStore,
    LayoutValidationError,
    rssi_to_distance_m,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "layout.json"


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

def test_set_then_get_position_round_trips(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    store.set_position("SIM-A", 1.5, -2.5)
    positions = store.get_scanner_positions()
    assert positions == {"SIM-A": (1.5, -2.5)}


def test_set_then_get_calibration_round_trips(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    store.set_calibration("SIM-A", rssi_at_1m=-59.0, path_loss_exponent=2.5)
    calibration = store.get_calibration("SIM-A")
    assert calibration is not None
    assert calibration.rssi_at_1m == -59.0
    assert calibration.path_loss_exponent == 2.5


def test_calibration_for_unknown_scanner_is_none(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    assert store.get_calibration("NEVER-SET") is None


def test_persistence_survives_reload(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    store.set_position("SIM-A", 3.0, 4.0)
    store.set_calibration("SIM-A", rssi_at_1m=-60.0, path_loss_exponent=2.0)

    reloaded = LayoutStore(path=store_path)
    assert reloaded.get_scanner_positions() == {"SIM-A": (3.0, 4.0)}
    calibration = reloaded.get_calibration("SIM-A")
    assert calibration.rssi_at_1m == -60.0
    assert calibration.path_loss_exponent == 2.0


def test_multiple_scanners_persist_independently(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    store.set_position("SIM-A", 0.0, 0.0)
    store.set_position("SIM-B", 5.0, 5.0)
    positions = store.get_scanner_positions()
    assert positions == {"SIM-A": (0.0, 0.0), "SIM-B": (5.0, 5.0)}


def test_file_is_valid_json_on_disk(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    store.set_position("SIM-A", 1.0, 2.0)
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert raw["positions"]["SIM-A"] == {"x": 1.0, "y": 2.0}


# ---------------------------------------------------------------------------
# Bounds validation - reject rather than corrupt
# ---------------------------------------------------------------------------

def test_set_position_rejects_out_of_bounds(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    with pytest.raises(LayoutValidationError):
        store.set_position("SIM-A", MAX_COORDINATE_METERS + 1.0, 0.0)
    # File must not exist / must not contain the rejected value.
    assert store.get_scanner_positions() == {}


def test_set_position_rejects_nan_and_inf(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    with pytest.raises(LayoutValidationError):
        store.set_position("SIM-A", float("nan"), 0.0)
    with pytest.raises(LayoutValidationError):
        store.set_position("SIM-A", float("inf"), 0.0)
    assert store.get_scanner_positions() == {}


def test_set_calibration_rejects_out_of_range_rssi(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    with pytest.raises(LayoutValidationError):
        store.set_calibration("SIM-A", rssi_at_1m=MIN_RSSI_AT_1M - 1.0, path_loss_exponent=2.0)
    with pytest.raises(LayoutValidationError):
        store.set_calibration("SIM-A", rssi_at_1m=MAX_RSSI_AT_1M + 1.0, path_loss_exponent=2.0)
    assert store.get_calibration("SIM-A") is None


def test_set_calibration_rejects_out_of_range_path_loss_exponent(store_path: Path) -> None:
    store = LayoutStore(path=store_path)
    with pytest.raises(LayoutValidationError):
        store.set_calibration("SIM-A", rssi_at_1m=-60.0, path_loss_exponent=MIN_PATH_LOSS_EXPONENT - 0.05)
    with pytest.raises(LayoutValidationError):
        store.set_calibration("SIM-A", rssi_at_1m=-60.0, path_loss_exponent=MAX_PATH_LOSS_EXPONENT + 1.0)
    assert store.get_calibration("SIM-A") is None


def test_invalid_write_does_not_corrupt_existing_file(store_path: Path) -> None:
    """A valid write followed by a rejected write must leave the on-disk
    file with only the valid data - no partial/garbage write."""
    store = LayoutStore(path=store_path)
    store.set_position("SIM-A", 1.0, 1.0)

    with pytest.raises(LayoutValidationError):
        store.set_position("SIM-B", float("nan"), 0.0)

    reloaded = LayoutStore(path=store_path)
    assert reloaded.get_scanner_positions() == {"SIM-A": (1.0, 1.0)}


# ---------------------------------------------------------------------------
# RSSI -> distance conversion
# ---------------------------------------------------------------------------

def test_rssi_to_distance_at_reference_point_is_one_meter(store_path: Path) -> None:
    from layout import ScannerCalibration

    calibration = ScannerCalibration(rssi_at_1m=-59.0, path_loss_exponent=2.0)
    distance = rssi_to_distance_m(-59.0, calibration)
    assert distance == pytest.approx(1.0)


def test_rssi_to_distance_weaker_signal_means_farther(store_path: Path) -> None:
    from layout import ScannerCalibration

    calibration = ScannerCalibration(rssi_at_1m=-59.0, path_loss_exponent=2.0)
    near = rssi_to_distance_m(-59.0, calibration)
    far = rssi_to_distance_m(-79.0, calibration)
    assert far > near
