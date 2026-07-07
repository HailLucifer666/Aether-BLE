"""Persisted scanner (x, y) floor-plan placement + per-scanner RSSI
calibration for the Phase 10 spatial fusion layer.

Persistence: a plain Python dict, persisted as flat JSON to
`~/.aether/layout.json` by default - mirroring room_adjacency.py's
RoomAdjacencyStore pattern (load eagerly at construction, rewrite the whole
file on every write) and realm.py's local-JSON-state convention. No new
persistence mechanism invented, per TECH_STACK.md.

Zero imports of election.py/aggregator.py/fusion_2d.py - this module only
deals in plain scanner ids, floats, and JSON. The caller (aggregator.py) is
responsible for converting a calibrated RSSI into a distance using the
log-distance path-loss model these calibration values imply:

    distance_m = 10 ** ((rssi_at_1m - calibrated_rssi) / (10 * path_loss_exponent))

(the standard log-distance/iBeacon proximity formula; rssi_at_1m is the
expected RSSI at 1 meter, path_loss_exponent captures how quickly signal
decays with distance in the deployment environment).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LAYOUT_PATH = Path.home() / ".aether" / "layout.json"

# Sane bound on floor-plan coordinates (meters). A malformed placeDevice
# message with |x| or |y| beyond this is rejected rather than silently
# persisted - no real indoor floor plan approaches this size.
MAX_COORDINATE_METERS = 1000.0

# Sane bounds on calibration inputs. rssiAt1m is a dBm reading (typical BLE
# range is roughly -30 to -100 dBm; widened generously so real hardware
# variance isn't rejected). pathLossExponent is a small positive multiplier
# in the log-distance model (free space ~2.0, indoor multipath commonly
# 1.5-6.0); zero or negative would make the distance formula undefined or
# nonsensical, so it's excluded.
MIN_RSSI_AT_1M = -100.0
MAX_RSSI_AT_1M = 0.0
MIN_PATH_LOSS_EXPONENT = 0.1
MAX_PATH_LOSS_EXPONENT = 10.0


class LayoutValidationError(ValueError):
    """Raised when a placeDevice/setCalibration input fails validation.

    Callers (aggregator.py's message handlers) catch this and log+drop the
    malformed message rather than letting it propagate and crash the tick
    loop - the same "never crash, always degrade" discipline used elsewhere
    in this codebase (e.g. MAX_TTS_TEXT_LENGTH truncation).
    """


@dataclass(frozen=True)
class ScannerPosition:
    x: float
    y: float


@dataclass(frozen=True)
class ScannerCalibration:
    rssi_at_1m: float
    path_loss_exponent: float


def _validate_finite(value: float, name: str) -> float:
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):  # NaN/inf check
        raise LayoutValidationError(f"{name} must be a finite number, got {value!r}.")
    return value


def _validate_coordinate(value: float, name: str) -> float:
    value = _validate_finite(value, name)
    if abs(value) > MAX_COORDINATE_METERS:
        raise LayoutValidationError(
            f"{name}={value} exceeds the {MAX_COORDINATE_METERS}m bound."
        )
    return value


def _validate_range(value: float, name: str, lo: float, hi: float) -> float:
    value = _validate_finite(value, name)
    if not (lo <= value <= hi):
        raise LayoutValidationError(f"{name}={value} must be within [{lo}, {hi}].")
    return value


class LayoutStore:
    """Persists and queries scanner (x, y) placement + calibration.

    Loaded eagerly at construction (same pattern as room_adjacency.py's
    RoomAdjacencyStore) and rewritten on every set_position()/
    set_calibration() call - placement/calibration edits are infrequent
    (a one-time setup wizard action), not a hot path requiring batched
    writes.
    """

    def __init__(self, path: Path | str = DEFAULT_LAYOUT_PATH) -> None:
        self.path = Path(path)
        self._positions: dict[str, ScannerPosition] = {}
        self._calibrations: dict[str, ScannerCalibration] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            positions_raw = raw.get("positions", {})
            calibrations_raw = raw.get("calibrations", {})
            self._positions = {
                str(scanner_id): ScannerPosition(x=float(v["x"]), y=float(v["y"]))
                for scanner_id, v in positions_raw.items()
            }
            self._calibrations = {
                str(scanner_id): ScannerCalibration(
                    rssi_at_1m=float(v["rssiAt1m"]),
                    path_loss_exponent=float(v["pathLossExponent"]),
                )
                for scanner_id, v in calibrations_raw.items()
            }

    def set_position(self, scanner_id: str, x: float, y: float) -> None:
        """Persist a scanner's (x, y) floor-plan position.

        Raises LayoutValidationError (not a crash) if x/y are not finite or
        exceed MAX_COORDINATE_METERS - the caller is expected to catch this
        and drop the message.
        """
        x = _validate_coordinate(x, "x")
        y = _validate_coordinate(y, "y")
        self._positions[scanner_id] = ScannerPosition(x=x, y=y)
        self._save()

    def set_calibration(self, scanner_id: str, rssi_at_1m: float, path_loss_exponent: float) -> None:
        """Persist a scanner's RSSI-at-1m / path-loss-exponent calibration.

        Raises LayoutValidationError (not a crash) if either value is not
        finite or outside its sane bound.
        """
        rssi_at_1m = _validate_range(rssi_at_1m, "rssiAt1m", MIN_RSSI_AT_1M, MAX_RSSI_AT_1M)
        path_loss_exponent = _validate_range(
            path_loss_exponent, "pathLossExponent", MIN_PATH_LOSS_EXPONENT, MAX_PATH_LOSS_EXPONENT
        )
        self._calibrations[scanner_id] = ScannerCalibration(
            rssi_at_1m=rssi_at_1m, path_loss_exponent=path_loss_exponent
        )
        self._save()

    def get_scanner_positions(self) -> dict[str, tuple[float, float]]:
        """All placed scanner positions as {scanner_id: (x, y)}."""
        return {sid: (pos.x, pos.y) for sid, pos in self._positions.items()}

    def get_calibration(self, scanner_id: str) -> ScannerCalibration | None:
        """This scanner's calibration, or None if never set."""
        return self._calibrations.get(scanner_id)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "positions": {
                sid: {"x": pos.x, "y": pos.y} for sid, pos in self._positions.items()
            },
            "calibrations": {
                sid: {"rssiAt1m": cal.rssi_at_1m, "pathLossExponent": cal.path_loss_exponent}
                for sid, cal in self._calibrations.items()
            },
        }
        # Atomic write: a process killed mid-write must never leave a
        # truncated/partial layout.json behind for the next load() to choke
        # on - write to a sibling temp file then os.replace() (atomic on both
        # POSIX and Windows), rather than truncating the real path in place.
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, self.path)


def rssi_to_distance_m(calibrated_rssi: float, calibration: ScannerCalibration) -> float:
    """Log-distance path-loss model: convert a calibrated RSSI (dBm) into a
    distance estimate (meters) using this scanner's rssi_at_1m/
    path_loss_exponent.

        distance_m = 10 ** ((rssi_at_1m - calibrated_rssi) / (10 * path_loss_exponent))

    The same formula election.ScannerState.calibrated_rssi()'s calibration
    offset implies (a scanner-specific additive dB correction folded into a
    reference RSSI-at-a-known-distance model) - this is the standard
    log-distance/iBeacon proximity conversion, not a new invented model.
    """
    exponent = (calibration.rssi_at_1m - calibrated_rssi) / (10.0 * calibration.path_loss_exponent)
    return 10.0 ** exponent
