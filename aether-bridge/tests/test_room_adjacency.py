"""Tests for room_adjacency.py's learned scanner-pair co-occurrence map.

Simulates N repeated contests between the same scanner pair with a
consistent chirp outcome, asserts the learned adjacency converges to the
correct answer, and asserts the lookup-before-chirp logic (should_skip_chirp)
correctly consults it - real pytest run, no mocking needed since this module
has no external I/O beyond a temp JSON file.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from room_adjacency import (
    MIN_AGREEMENT_RATIO_FOR_CONFIDENCE,
    MIN_OBSERVATIONS_FOR_CONFIDENCE,
    RoomAdjacencyStore,
    should_skip_chirp,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "room_adjacency.json"


def test_unobserved_pair_is_not_confident(store_path: Path) -> None:
    store = RoomAdjacencyStore(path=store_path)
    result = store.lookup("A", "B")
    assert result.confident is False
    assert result.observations == 0


def test_pair_key_is_order_independent(store_path: Path) -> None:
    store = RoomAdjacencyStore(path=store_path)
    store.record("A", "B", same_room=True)
    forward = store.lookup("A", "B")
    backward = store.lookup("B", "A")
    assert forward.observations == backward.observations == 1
    assert forward.same_room == backward.same_room is True


def test_converges_to_correct_same_room_answer(store_path: Path) -> None:
    # N repeated contests between the same pair, always same_room=True -
    # should converge to confident+True well before an unreasonable number
    # of observations.
    store = RoomAdjacencyStore(path=store_path)
    n_repeats = 20
    for _ in range(n_repeats):
        store.record("A", "B", same_room=True)

    result = store.lookup("A", "B")
    assert result.observations == n_repeats
    assert result.same_room_count == n_repeats
    assert result.confident is True
    assert result.same_room is True


def test_converges_to_correct_different_room_answer(store_path: Path) -> None:
    store = RoomAdjacencyStore(path=store_path)
    n_repeats = 20
    for _ in range(n_repeats):
        store.record("A", "C", same_room=False)

    result = store.lookup("A", "C")
    assert result.confident is True
    assert result.same_room is False


def test_not_confident_below_min_observations(store_path: Path) -> None:
    store = RoomAdjacencyStore(path=store_path)
    for _ in range(MIN_OBSERVATIONS_FOR_CONFIDENCE - 1):
        store.record("A", "B", same_room=True)

    result = store.lookup("A", "B")
    assert result.observations == MIN_OBSERVATIONS_FOR_CONFIDENCE - 1
    assert result.confident is False


def test_confident_at_min_observations_with_full_agreement(store_path: Path) -> None:
    store = RoomAdjacencyStore(path=store_path)
    for _ in range(MIN_OBSERVATIONS_FOR_CONFIDENCE):
        store.record("A", "B", same_room=True)

    result = store.lookup("A", "B")
    assert result.observations == MIN_OBSERVATIONS_FOR_CONFIDENCE
    assert result.confident is True


def test_inconsistent_outcomes_never_become_confident(store_path: Path) -> None:
    # Alternating True/False outcomes - agreement ratio stays near 0.5,
    # well below MIN_AGREEMENT_RATIO_FOR_CONFIDENCE, so even with plenty of
    # observations this pair must never become confident (a genuinely
    # flaky/ambiguous pair, e.g. a half-open door, should keep chirping).
    store = RoomAdjacencyStore(path=store_path)
    for i in range(40):
        store.record("A", "B", same_room=(i % 2 == 0))

    result = store.lookup("A", "B")
    assert result.observations == 40
    assert result.confident is False


def test_confidence_can_recover_after_a_correction(store_path: Path) -> None:
    # A pair starts consistently True, then a run of False observations
    # should be able to shift the learned majority - the map corrects itself
    # rather than being permanently locked by early evidence.
    store = RoomAdjacencyStore(path=store_path)
    for _ in range(5):
        store.record("A", "B", same_room=True)
    assert store.lookup("A", "B").same_room is True

    # Enough False observations to outweigh the initial 5 True ones and
    # clear MIN_AGREEMENT_RATIO_FOR_CONFIDENCE (0.9): need
    # false_count / (5 + false_count) >= 0.9 -> false_count >= 45.
    for _ in range(50):
        store.record("A", "B", same_room=False)

    result = store.lookup("A", "B")
    assert result.same_room is False
    assert result.confident is True


def test_should_skip_chirp_delegates_to_lookup(store_path: Path) -> None:
    store = RoomAdjacencyStore(path=store_path)
    for _ in range(MIN_OBSERVATIONS_FOR_CONFIDENCE):
        store.record("A", "B", same_room=True)

    lookup_result = store.lookup("A", "B")
    skip_result = should_skip_chirp(store, "A", "B")
    assert skip_result == lookup_result


def test_lookup_before_chirp_simulation_reduces_chirp_frequency(store_path: Path) -> None:
    """Simulates the real usage pattern the aggregator would follow: before
    firing a chirp, consult should_skip_chirp(); only fire (and record) a
    real chirp when not yet confident. Asserts the number of real chirps
    fired drops to near-zero once the pair is confidently learned, across
    many repeated contests - the actual behavior room_adjacency exists to
    produce (fewer chirps over time for a stable pair)."""
    store = RoomAdjacencyStore(path=store_path)
    n_contests = 50
    chirps_fired = 0
    ground_truth_same_room = True

    for _ in range(n_contests):
        lookup = should_skip_chirp(store, "A", "B")
        if lookup.confident:
            # Skip the real chirp - trust the learned adjacency.
            continue
        # Fire a "real" chirp (simulated: always agrees with ground truth,
        # matching the PRD's "consistent chirp outcome" scenario) and record it.
        chirps_fired += 1
        store.record("A", "B", same_room=ground_truth_same_room)

    final_lookup = store.lookup("A", "B")
    assert final_lookup.confident is True
    assert final_lookup.same_room == ground_truth_same_room
    # Only the first MIN_OBSERVATIONS_FOR_CONFIDENCE contests should have
    # fired a real chirp; every subsequent contest should have skipped.
    assert chirps_fired == MIN_OBSERVATIONS_FOR_CONFIDENCE
    assert chirps_fired < n_contests


def test_persists_across_store_instances(store_path: Path) -> None:
    store1 = RoomAdjacencyStore(path=store_path)
    for _ in range(MIN_OBSERVATIONS_FOR_CONFIDENCE):
        store1.record("A", "B", same_room=True)

    # A fresh instance reading the same path should see the same learned
    # state - mirrors beacon_auth.BeaconCounterStore's persistence contract.
    store2 = RoomAdjacencyStore(path=store_path)
    result = store2.lookup("A", "B")
    assert result.confident is True
    assert result.same_room is True


def test_persisted_file_is_flat_json(store_path: Path) -> None:
    store = RoomAdjacencyStore(path=store_path)
    store.record("A", "B", same_room=True)
    assert store_path.exists()
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert "A|B" in raw
    assert raw["A|B"]["observations"] == 1
    assert raw["A|B"]["same_room"] == 1


def test_default_path_is_under_dot_aether_home() -> None:
    from room_adjacency import DEFAULT_ADJACENCY_PATH

    assert DEFAULT_ADJACENCY_PATH.parent.name == ".aether"
    assert DEFAULT_ADJACENCY_PATH.name == "room_adjacency.json"
