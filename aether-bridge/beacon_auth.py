"""Authenticated beacon payload: build and verify.

Wire format (19 bytes total, big-endian, fits BLE manufacturer-data):

    [2B magic | 1B ver | 4B uid_hash | 4B counter | 8B HMAC-SHA256(realm_key, uid_hash||counter)]

The HMAC is truncated to its first 8 bytes to fit the BLE manufacturer-data
budget; this is a standard truncated-MAC construction and still provides a
64-bit forgery resistance, which is adequate for this phase's LAN-trust
threat model (see PROTOCOL.md Security Annex).

Counter is monotonic per uid_hash and persisted to disk
(`~/.aether/beacon_counter.json` by default) so a process/device restart does
not reopen a replay window at counter=0 (confirmed product decision).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

MAGIC = b"\xAE\x74"  # "Aether" shorthand marker
CURRENT_VERSION = 1
PAYLOAD_LENGTH = 19  # 2 + 1 + 4 + 4 + 8
MAC_LENGTH = 8

DEFAULT_COUNTER_PATH = Path.home() / ".aether" / "beacon_counter.json"


def uid_hash_from_name(name: str) -> int:
    """Derive the 32-bit uid_hash carried in the beacon payload from a
    beacon's stable identifying name.

    Single-user beacons only this phase (per confirmed product scope) - the
    uid_hash field exists in the wire format for future multi-user support,
    but election/ownership logic (election.py) is untouched this phase.
    """
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _compute_mac(realm_key: bytes, uid_hash: int, counter: int) -> bytes:
    message = uid_hash.to_bytes(4, "big") + counter.to_bytes(4, "big")
    full_mac = hmac.new(realm_key, message, sha256).digest()
    return full_mac[:MAC_LENGTH]


def build_beacon_payload(realm_key: bytes, uid_hash: int, counter: int, ver: int = CURRENT_VERSION) -> bytes:
    """Build the 19-byte authenticated beacon payload."""
    mac = _compute_mac(realm_key, uid_hash, counter)
    return (
        MAGIC
        + ver.to_bytes(1, "big")
        + uid_hash.to_bytes(4, "big")
        + counter.to_bytes(4, "big")
        + mac
    )


@dataclass
class BeaconVerifyResult:
    ok: bool
    uid_hash: int | None = None
    counter: int | None = None
    reason: str | None = None  # None when ok; else one of the rejection reasons below


def verify_beacon(payload: bytes, realm_key: bytes, last_counter: int) -> BeaconVerifyResult:
    """Verify an authenticated beacon payload.

    Rejects (in order checked) on: wrong length, wrong magic, unsupported
    version, replayed/stale counter (<= last_counter), or HMAC mismatch.
    Counter is checked before HMAC so a replayed-but-otherwise-valid payload
    is rejected without needing a fresh HMAC computation - both are cheap
    here, but this ordering matches the documented rule (§ replay OR hmac
    mismatch => reject) without leaking which failed via timing in any
    security-meaningful way for this phase's threat model.
    """
    if len(payload) != PAYLOAD_LENGTH:
        return BeaconVerifyResult(ok=False, reason="bad_length")

    if payload[0:2] != MAGIC:
        return BeaconVerifyResult(ok=False, reason="bad_magic")

    ver = payload[2]
    if ver != CURRENT_VERSION:
        return BeaconVerifyResult(ok=False, reason="bad_version")

    uid_hash = int.from_bytes(payload[3:7], "big")
    counter = int.from_bytes(payload[7:11], "big")
    received_mac = payload[11:19]

    if counter <= last_counter:
        return BeaconVerifyResult(ok=False, reason="replay")

    expected_mac = _compute_mac(realm_key, uid_hash, counter)
    if not hmac.compare_digest(received_mac, expected_mac):
        return BeaconVerifyResult(ok=False, reason="hmac_mismatch")

    return BeaconVerifyResult(ok=True, uid_hash=uid_hash, counter=counter)


def verify_beacon_any_key(payload: bytes, candidate_keys: list[bytes], last_counter: int) -> BeaconVerifyResult:
    """Verify against a list of candidate realm keys (current key first, then
    grace-window history), so a beacon signed just before a realm key
    rotation is not rejected mid-flight - see realm.py's grace-window design
    and PROTOCOL.md Security Annex.

    Tries each key with `verify_beacon` in order and returns the first
    success. If every key fails, returns the first candidate's failure
    result (length/magic/version/replay reasons are identical regardless of
    which key was tried, since those checks run before the HMAC compare;
    only "hmac_mismatch" can legitimately differ per key, and if all keys
    produce it the payload is simply not authentic for this realm).
    """
    if not candidate_keys:
        return BeaconVerifyResult(ok=False, reason="no_candidate_keys")

    first_result: BeaconVerifyResult | None = None
    for key in candidate_keys:
        result = verify_beacon(payload, key, last_counter)
        if first_result is None:
            first_result = result
        if result.ok:
            return result
    return first_result


class BeaconCounterStore:
    """Persists the last-accepted counter per uid_hash to a flat JSON file.

    Loaded eagerly at construction and rewritten on every update - counters
    update at most once per beacon interval (sub-second), so this is not a
    hot path requiring batching.
    """

    def __init__(self, path: Path | str = DEFAULT_COUNTER_PATH) -> None:
        self.path = Path(path)
        self._counters: dict[str, int] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._counters = {str(k): int(v) for k, v in raw.items()}

    def get_last_counter(self, uid_hash: int) -> int:
        return self._counters.get(str(uid_hash), 0)

    def set_last_counter(self, uid_hash: int, counter: int) -> None:
        self._counters[str(uid_hash)] = counter
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._counters), encoding="utf-8")
