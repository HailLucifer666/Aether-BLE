"""Tests for beacon_auth.py (authenticated beacon payload build/verify).

Payload format (19 bytes, big-endian):
    [2B magic | 1B ver | 4B uid_hash | 4B counter | 8B HMAC-SHA256(realm_key, uid_hash||counter)]

Run with: pytest tests/test_beacon_auth.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from beacon_auth import (
    PAYLOAD_LENGTH,
    BeaconCounterStore,
    build_beacon_payload,
    verify_beacon,
)

REALM_KEY = b"\x42" * 32
UID_HASH = 0xDEADBEEF


def test_build_beacon_payload_is_correct_length() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=1)
    assert len(payload) == PAYLOAD_LENGTH == 19


def test_verify_beacon_accepts_valid_payload() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=5)
    result = verify_beacon(payload, REALM_KEY, last_counter=0)
    assert result.ok is True
    assert result.uid_hash == UID_HASH
    assert result.counter == 5


def test_verify_beacon_rejects_replayed_equal_counter() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=5)
    result = verify_beacon(payload, REALM_KEY, last_counter=5)
    assert result.ok is False
    assert result.reason == "replay"


def test_verify_beacon_rejects_stale_lower_counter() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=3)
    result = verify_beacon(payload, REALM_KEY, last_counter=10)
    assert result.ok is False
    assert result.reason == "replay"


def test_verify_beacon_rejects_tampered_hmac() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=5)
    tampered = payload[:-1] + bytes([payload[-1] ^ 0xFF])
    result = verify_beacon(tampered, REALM_KEY, last_counter=0)
    assert result.ok is False
    assert result.reason == "hmac_mismatch"


def test_verify_beacon_rejects_wrong_realm_key() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=5)
    result = verify_beacon(payload, b"\x99" * 32, last_counter=0)
    assert result.ok is False
    assert result.reason == "hmac_mismatch"


def test_verify_beacon_rejects_wrong_magic() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=5)
    corrupted = b"\x00\x00" + payload[2:]
    result = verify_beacon(corrupted, REALM_KEY, last_counter=0)
    assert result.ok is False
    assert result.reason == "bad_magic"


def test_verify_beacon_rejects_wrong_length() -> None:
    result = verify_beacon(b"\x01\x02\x03", REALM_KEY, last_counter=0)
    assert result.ok is False
    assert result.reason == "bad_length"


def test_verify_beacon_rejects_unsupported_version() -> None:
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=5, ver=99)
    result = verify_beacon(payload, REALM_KEY, last_counter=0)
    assert result.ok is False
    assert result.reason == "bad_version"


def test_counter_store_persists_across_reload(tmp_path) -> None:
    counter_path = tmp_path / "beacon_counter.json"
    store = BeaconCounterStore(counter_path)
    store.set_last_counter(UID_HASH, 42)

    reloaded = BeaconCounterStore(counter_path)
    assert reloaded.get_last_counter(UID_HASH) == 42


def test_counter_store_defaults_to_zero_for_unknown_uid(tmp_path) -> None:
    store = BeaconCounterStore(tmp_path / "beacon_counter.json")
    assert store.get_last_counter(0x12345) == 0


def test_counter_store_survives_restart_no_replay_window_reopens(tmp_path) -> None:
    """Confirmed product decision: counter persists across process restart -
    a fresh BeaconCounterStore instance must not treat a previously-seen
    counter as replayable-from-zero.
    """
    counter_path = tmp_path / "beacon_counter.json"
    store = BeaconCounterStore(counter_path)
    payload = build_beacon_payload(REALM_KEY, UID_HASH, counter=100)
    result = verify_beacon(payload, REALM_KEY, last_counter=store.get_last_counter(UID_HASH))
    assert result.ok is True
    store.set_last_counter(UID_HASH, result.counter)

    # Simulate process restart: new store instance reads the same file.
    restarted_store = BeaconCounterStore(counter_path)
    replay_result = verify_beacon(
        payload, REALM_KEY, last_counter=restarted_store.get_last_counter(UID_HASH)
    )
    assert replay_result.ok is False
    assert replay_result.reason == "replay"
