"""2-D Kalman filter fusing per-scanner distance estimates into a single
(x, y) position + covariance per tracked user. ADVISORY ONLY this phase -
not consulted by aggregator._owner or election.py's proven 1-D hysteresis
arbitration (see docs/phase9/PRD.md's explicit out-of-scope: 2-D tracking
does not replace tier-1 election this phase). This is the data layer a
future dashboard (Phase 10) will visualize.

Hand-rolled with plain numpy linear algebra per TECH_STACK.md's constraint
(no filterpy/new dependency) - a 2-D constant-velocity Kalman filter is a
well-understood ~40-line piece of matrix math:

State vector x = [px, py, vx, vy]^T (position + velocity in meters/sec).
Each scanner update is a nonlinear (range-only) measurement of distance from
a known scanner position, linearized around the current state estimate (an
Extended Kalman Filter update) since a plain linear KF cannot directly
consume a scalar distance-from-a-point measurement.

Zero imports of ranging.py/election.py/aggregator.py - this module only
consumes plain floats (scanner positions + distances), matching this
codebase's existing discipline of keeping algorithmic modules free of
cross-module coupling until something else explicitly wires them together
(the wiring, if/when it happens, is Phase 10's job per FEATURES.md).
"""

from dataclasses import dataclass, field

import numpy as np

# Process noise: how much we expect true velocity to randomly change between
# ticks (m/s^2-ish acceleration noise), reflected into the state transition's
# process noise covariance Q. Larger = filter trusts new measurements more
# and tracks a maneuvering target faster, at the cost of more jitter.
PROCESS_NOISE_ACCEL_STD = 0.5

# Measurement noise: assumed standard deviation (in meters) of a single
# scanner's RSSI-derived (or chirp-derived) distance estimate. RSSI-based
# distance is fairly noisy at typical indoor multipath; chirp ToF-derived
# distance is much more precise, so update() accepts a per-call override.
DEFAULT_MEASUREMENT_NOISE_STD_M = 1.0
CHIRP_MEASUREMENT_NOISE_STD_M = 0.05

# Initial state uncertainty (large - we genuinely don't know where a newly
# tracked user is on first observation).
INITIAL_POSITION_VARIANCE = 25.0  # (5m)^2
INITIAL_VELOCITY_VARIANCE = 4.0  # (2 m/s)^2


@dataclass
class Track2D:
    """A single user's 2-D Kalman track: state + covariance + bookkeeping.

    `state` is [px, py, vx, vy] (meters, meters/sec). `covariance` is the 4x4
    state covariance matrix. `last_tick` is the tick this track was last
    updated at, used by `predict()` to compute the elapsed-time step `dt`.
    """

    state: np.ndarray
    covariance: np.ndarray
    last_tick: int
    tick_duration_s: float = 0.4

    @staticmethod
    def initialize(x0: float, y0: float, tick: int, tick_duration_s: float = 0.4) -> "Track2D":
        """Start a new track at an initial (rough) position guess.

        Velocity starts at zero (no prior motion information); position/
        velocity variances start large per INITIAL_*_VARIANCE so the first
        few real measurements can move the estimate freely rather than being
        fought by an overconfident prior.
        """
        state = np.array([x0, y0, 0.0, 0.0], dtype=np.float64)
        covariance = np.diag(
            [
                INITIAL_POSITION_VARIANCE,
                INITIAL_POSITION_VARIANCE,
                INITIAL_VELOCITY_VARIANCE,
                INITIAL_VELOCITY_VARIANCE,
            ]
        ).astype(np.float64)
        return Track2D(state=state, covariance=covariance, last_tick=tick, tick_duration_s=tick_duration_s)

    @property
    def position(self) -> tuple[float, float]:
        return float(self.state[0]), float(self.state[1])

    @property
    def position_covariance(self) -> np.ndarray:
        """The 2x2 position sub-block of the full 4x4 covariance - what a
        dashboard would draw as an uncertainty ellipse around (x, y)."""
        return self.covariance[:2, :2]

    @property
    def position_uncertainty_radius_m(self) -> float:
        """A single honest scalar uncertainty radius (meters): the sqrt of
        the larger of the two position-variance eigenvalues, i.e. the
        semi-major axis of the 1-sigma uncertainty ellipse. Used for the
        "honest uncertainty radius" the PRD's user story calls for."""
        eigenvalues = np.linalg.eigvalsh(self.position_covariance)
        return float(np.sqrt(max(eigenvalues.max(), 0.0)))


def _transition_matrices(dt: float) -> tuple[np.ndarray, np.ndarray]:
    """Build the constant-velocity state transition F and process noise Q
    for elapsed time `dt` (seconds).

    F models: position += velocity * dt, velocity unchanged (constant
    velocity model - the acceleration "noise" below is what lets it adapt).
    Q is the discretized white-noise-acceleration process noise, the
    standard constant-velocity KF process noise model.
    """
    f = np.array(
        [
            [1.0, 0.0, dt, 0.0],
            [0.0, 1.0, 0.0, dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    q_accel = PROCESS_NOISE_ACCEL_STD**2
    dt2 = dt * dt
    dt3 = dt2 * dt
    dt4 = dt3 * dt
    # Standard discretized white-noise-acceleration model block per axis:
    # [[dt^4/4, dt^3/2], [dt^3/2, dt^2]] * q_accel, applied to (pos, vel) of
    # each of the two independent axes (x and y).
    q_block = q_accel * np.array([[dt4 / 4.0, dt3 / 2.0], [dt3 / 2.0, dt2]])
    q = np.zeros((4, 4))
    q[0, 0] = q_block[0, 0]
    q[0, 2] = q_block[0, 1]
    q[2, 0] = q_block[1, 0]
    q[2, 2] = q_block[1, 1]
    q[1, 1] = q_block[0, 0]
    q[1, 3] = q_block[0, 1]
    q[3, 1] = q_block[1, 0]
    q[3, 3] = q_block[1, 1]
    return f, q


def predict(track: Track2D, tick: int) -> Track2D:
    """Advance the track's state estimate to `tick` with no new measurement.

    Standard KF predict step: x' = F x, P' = F P F^T + Q. If `tick` is not
    ahead of `track.last_tick`, returns the track unchanged (nothing to
    predict backwards or in place).
    """
    elapsed_ticks = tick - track.last_tick
    if elapsed_ticks <= 0:
        return track
    dt = elapsed_ticks * track.tick_duration_s
    f, q = _transition_matrices(dt)
    new_state = f @ track.state
    new_covariance = f @ track.covariance @ f.T + q
    return Track2D(
        state=new_state,
        covariance=new_covariance,
        last_tick=tick,
        tick_duration_s=track.tick_duration_s,
    )


def update(
    track: Track2D,
    scanner_position: tuple[float, float],
    measured_distance_m: float,
    measurement_noise_std_m: float = DEFAULT_MEASUREMENT_NOISE_STD_M,
) -> Track2D:
    """Fuse one range-only measurement (distance from a known scanner
    position) into the track via an Extended Kalman Filter update.

    The measurement model h(x) = ||(px, py) - scanner_position|| is
    nonlinear in the state, so this linearizes around the current state
    estimate: H = d(h)/d(state) evaluated at track.state (the standard EKF
    approach for range-only tracking - the same technique used for GPS/radar
    trilateration). Caller supplies `measurement_noise_std_m` per-call so a
    chirp-derived distance (very precise) and an RSSI-derived distance
    (noisy) get correctly different measurement noise (see
    CHIRP_MEASUREMENT_NOISE_STD_M vs DEFAULT_MEASUREMENT_NOISE_STD_M).
    """
    px, py = track.state[0], track.state[1]
    sx, sy = scanner_position
    dx = px - sx
    dy = py - sy
    predicted_distance = float(np.hypot(dx, dy))

    # Guard against the degenerate case where the estimated position sits
    # exactly on the scanner (division by zero in the Jacobian); nudge the
    # predicted distance to a small epsilon rather than crashing - a rare
    # edge case, not a fundamental failure of the filter.
    if predicted_distance < 1e-6:
        predicted_distance = 1e-6

    h_jacobian = np.array(
        [[dx / predicted_distance, dy / predicted_distance, 0.0, 0.0]]
    )  # shape (1, 4)

    innovation = np.array([measured_distance_m - predicted_distance])  # shape (1,)
    r = np.array([[measurement_noise_std_m**2]])  # shape (1, 1)

    p = track.covariance
    s = h_jacobian @ p @ h_jacobian.T + r  # shape (1, 1), innovation covariance
    kalman_gain = p @ h_jacobian.T @ np.linalg.inv(s)  # shape (4, 1)

    new_state = track.state + (kalman_gain @ innovation)
    identity = np.eye(4)
    new_covariance = (identity - kalman_gain @ h_jacobian) @ p

    return Track2D(
        state=new_state,
        covariance=new_covariance,
        last_tick=track.last_tick,
        tick_duration_s=track.tick_duration_s,
    )


def update_from_scanner_distances(
    track: Track2D,
    tick: int,
    scanner_positions: dict[str, tuple[float, float]],
    scanner_distances: dict[str, float],
    chirp_scanner_id: str | None = None,
) -> Track2D:
    """Convenience wrapper: predict to `tick`, then fold in one measurement
    per scanner in `scanner_distances` (only for scanners present in
    `scanner_positions` - a distance reading for an unknown scanner position
    is silently skipped, since there's no geometry to fuse it against).

    `chirp_scanner_id`, if given, marks which scanner's distance reading (if
    any) came from a chirp ToF rather than RSSI - that single reading gets
    CHIRP_MEASUREMENT_NOISE_STD_M instead of the default RSSI-grade noise,
    reflecting chirp ToF's much higher precision (cm vs meter-grade).
    """
    advanced = predict(track, tick)
    updated = advanced
    for scanner_id, distance_m in scanner_distances.items():
        position = scanner_positions.get(scanner_id)
        if position is None:
            continue
        noise_std = (
            CHIRP_MEASUREMENT_NOISE_STD_M
            if scanner_id == chirp_scanner_id
            else DEFAULT_MEASUREMENT_NOISE_STD_M
        )
        updated = update(updated, position, distance_m, measurement_noise_std_m=noise_std)
    return updated


@dataclass
class FusionTracker:
    """Per-user Track2D registry - the advisory tracking layer's entry
    point. Not wired into aggregator._owner this phase (PRD's explicit
    out-of-scope); a future call site (Phase 10) would hold one of these
    per tracked beacon identity and call `update()` each tick alongside the
    existing election tick, purely for dashboard visualization."""

    tracks: dict[str, Track2D] = field(default_factory=dict)

    def update(
        self,
        user_id: str,
        tick: int,
        scanner_positions: dict[str, tuple[float, float]],
        scanner_distances: dict[str, float],
        chirp_scanner_id: str | None = None,
    ) -> Track2D:
        """Update (creating if necessary) the track for `user_id` and return
        the resulting Track2D. First-ever observation initializes the track
        at a rough centroid of the reporting scanners (better than an
        arbitrary origin) so the first covariance collapse isn't fighting a
        wildly wrong prior."""
        existing = self.tracks.get(user_id)
        if existing is None:
            x0, y0 = _initial_guess(scanner_positions, scanner_distances)
            existing = Track2D.initialize(x0, y0, tick)
        updated = update_from_scanner_distances(
            existing, tick, scanner_positions, scanner_distances, chirp_scanner_id
        )
        self.tracks[user_id] = updated
        return updated



# When every known scanner sits on the same line (collinear - e.g. a common
#2-scanner room layout with both scanners against one wall), the range-only
# EKF's measurement Jacobian has an exactly-zero perpendicular component at
# any state sitting on that same line: dy/d(state) = 0 for every scanner,
# so no measurement can ever push the estimate off the line once it starts
# on it, while process noise still inflates that direction's variance every
# predict() with nothing to counteract it - an unbounded, silent divergence.
# Seeding the initial guess with a small deterministic perpendicular offset
# breaks that degenerate fixed point so real innovations can correct it in
# either direction, without making the module non-deterministic (no RNG).
_COLLINEAR_SEED_OFFSET_M = 0.5


def _initial_guess(
    scanner_positions: dict[str, tuple[float, float]],
    scanner_distances: dict[str, float],
) -> tuple[float, float]:
    """Rough first-guess position: the centroid of every scanner that has
    both a known position and a distance reading (nudged off-axis if those
    scanners are collinear - see _COLLINEAR_SEED_OFFSET_M above), or the
    origin if none."""
    known = [
        scanner_positions[sid]
        for sid in scanner_distances
        if sid in scanner_positions
    ]
    if not known:
        return (0.0, 0.0)
    xs = [p[0] for p in known]
    ys = [p[1] for p in known]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)

    if _is_collinear(known):
        return (cx, cy + _COLLINEAR_SEED_OFFSET_M)
    return (cx, cy)


def _is_collinear(points: list[tuple[float, float]]) -> bool:
    """True if all points share a line (trivially true for <=2 points, which
    covers the common 2-scanner room case the PRD itself targets)."""
    if len(points) <= 2:
        return True
    (x0, y0), (x1, y1) = points[0], points[1]
    for x, y in points[2:]:
        # Cross-product of (p1-p0) and (p-p0); nonzero means off the line.
        cross = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
        if abs(cross) > 1e-9:
            return False
    return True
