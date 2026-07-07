"""Learned scanner-pair room-adjacency co-occurrence map.

Consulted BEFORE firing a new real chirp: once a scanner pair's
room-containment relationship (do these two scanners consistently hear each
other's chirps, i.e. are they in the same room?) is confidently known from
enough prior chirp outcomes, skip firing a new chirp and synthesize the
ChirpResult directly from the learned adjacency instead - reducing the tier-2
duty cycle without changing ranging.py's fusion contract (per PRD/
ARCHITECTURE.md: "an optimization in front of the chirp, not a replacement").

Persistence: a plain Python dict-based co-occurrence count, persisted as
flat JSON to `~/.aether/room_adjacency.json` by default - mirroring
beacon_auth.py's BeaconCounterStore pattern (load eagerly at construction,
rewrite on every update) and realm.py's local-JSON-state convention. No new
persistence mechanism invented, per TECH_STACK.md.

Zero imports of ranging.py/aggregator.py - this module only deals in plain
scanner-id pairs and booleans (did pair (A, B) hear each other's chirp this
round?). The caller (a future aggregator wiring, not built this phase per
ARCHITECTURE.md's "consulted before firing a new real chirp" data-flow
description) is responsible for translating a Contest/ChirpResult into the
record() call and translating a confident lookup() back into a synthesized
ChirpResult - keeping this module itself free of ranging.py's dataclasses.
"""

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ADJACENCY_PATH = Path.home() / ".aether" / "room_adjacency.json"

# A pair's learned adjacency is "confident" once it has been observed at
# least this many times AND its outcomes agree at least this consistently -
# below either threshold, keep firing real chirps rather than trusting a
# thin or contradictory history. Mirrors the deliberately conservative
# posture of election.py's HYSTERESIS_CONSECUTIVE: don't trust a fact from
# too little or too inconsistent evidence.
MIN_OBSERVATIONS_FOR_CONFIDENCE = 5
MIN_AGREEMENT_RATIO_FOR_CONFIDENCE = 0.9


def _pair_key(scanner_a: str, scanner_b: str) -> str:
    """Order-independent key for a scanner pair: (A, B) and (B, A) are the
    same physical relationship, so sort lexically for a single canonical key."""
    lo, hi = sorted((scanner_a, scanner_b))
    return f"{lo}|{hi}"


@dataclass(frozen=True)
class AdjacencyLookup:
    """Result of consulting the learned map for a scanner pair.

    `confident` is True only once MIN_OBSERVATIONS_FOR_CONFIDENCE and
    MIN_AGREEMENT_RATIO_FOR_CONFIDENCE are both satisfied. `same_room` is the
    learned majority outcome (meaningless/False when not confident - callers
    must check `confident` first). `observations` / `same_room_count` are the
    raw counts, exposed for diagnostics/dashboard display.
    """

    confident: bool
    same_room: bool
    observations: int
    same_room_count: int


class RoomAdjacencyStore:
    """Persists and queries the learned scanner-pair co-occurrence map.

    Loaded eagerly at construction (same pattern as beacon_auth.py's
    BeaconCounterStore) and rewritten on every `record()` call - chirp
    outcomes are infrequent enough (one per contest episode, per
    aggregator.py's _ranging_loop docstring) that this is not a hot path
    requiring batched writes.
    """

    def __init__(self, path: Path | str = DEFAULT_ADJACENCY_PATH) -> None:
        self.path = Path(path)
        # Keyed by canonical pair key -> {"observations": int, "same_room": int}.
        self._pairs: dict[str, dict[str, int]] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._pairs = {
                str(key): {"observations": int(v["observations"]), "same_room": int(v["same_room"])}
                for key, v in raw.items()
            }

    def record(self, scanner_a: str, scanner_b: str, same_room: bool) -> None:
        """Strengthen (or correct) the learned adjacency for this pair after
        a real chirp result. Called once per real chirp outcome - see
        ARCHITECTURE.md's data flow: "... fire the real chirp as normal,
        then room_adjacency.record(incumbent, challenger, result) to
        strengthen (or correct) the learned map for next time."
        """
        key = _pair_key(scanner_a, scanner_b)
        entry = self._pairs.setdefault(key, {"observations": 0, "same_room": 0})
        entry["observations"] += 1
        if same_room:
            entry["same_room"] += 1
        self._save()

    def lookup(self, scanner_a: str, scanner_b: str) -> AdjacencyLookup:
        """Consult the learned map for this pair. Returns not-confident
        (observations=0) if the pair has never been observed."""
        key = _pair_key(scanner_a, scanner_b)
        entry = self._pairs.get(key)
        if entry is None or entry["observations"] == 0:
            return AdjacencyLookup(confident=False, same_room=False, observations=0, same_room_count=0)

        observations = entry["observations"]
        same_room_count = entry["same_room"]
        agreement_ratio = max(same_room_count, observations - same_room_count) / observations
        majority_same_room = same_room_count > (observations - same_room_count)

        confident = (
            observations >= MIN_OBSERVATIONS_FOR_CONFIDENCE
            and agreement_ratio >= MIN_AGREEMENT_RATIO_FOR_CONFIDENCE
        )
        return AdjacencyLookup(
            confident=confident,
            same_room=majority_same_room,
            observations=observations,
            same_room_count=same_room_count,
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._pairs), encoding="utf-8")


def should_skip_chirp(store: RoomAdjacencyStore, scanner_a: str, scanner_b: str) -> AdjacencyLookup:
    """Consult the store and answer the actual question the ranging loop
    seam needs answered: is this pair's containment relationship already
    confidently known, so a new real chirp can be skipped this round? A thin
    wrapper over `lookup()` kept as its own function so a future aggregator
    call site (per ARCHITECTURE.md's "consulted before firing a new real
    chirp") has a single, obviously-named entry point to call, rather than
    every caller re-deriving "confident" meaning "skip" inline.
    """
    return store.lookup(scanner_a, scanner_b)
