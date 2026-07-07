"""Tests for realm.py (realm key storage/versioning, grace-window verify).

Run with: pytest tests/test_realm.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from realm import Realm, load_or_create_realm


def test_load_or_create_realm_creates_new_realm_key(tmp_path) -> None:
    realm_path = tmp_path / "realm.json"
    realm = load_or_create_realm(realm_path)

    assert isinstance(realm, Realm)
    assert realm.key_version == 1
    assert len(realm.current_key) == 32  # 256-bit HMAC key
    assert realm_path.exists()


def test_load_or_create_realm_persists_and_reloads_same_key(tmp_path) -> None:
    realm_path = tmp_path / "realm.json"
    first = load_or_create_realm(realm_path)
    second = load_or_create_realm(realm_path)

    assert first.current_key == second.current_key
    assert first.key_version == second.key_version


def test_rotate_key_increments_version_and_keeps_grace_history(tmp_path) -> None:
    realm_path = tmp_path / "realm.json"
    realm = load_or_create_realm(realm_path)
    old_key = realm.current_key
    old_version = realm.key_version

    realm.rotate_key()

    assert realm.key_version == old_version + 1
    assert realm.current_key != old_key
    # old key still verifiable within the grace window
    assert realm.verify_key(old_key, old_version) is True


def test_verify_key_accepts_current_version() -> None:
    realm = Realm(key_version=1, current_key=b"\x01" * 32, key_history={1: b"\x01" * 32})
    assert realm.verify_key(b"\x01" * 32, 1) is True


def test_verify_key_rejects_wrong_key_for_version() -> None:
    realm = Realm(key_version=1, current_key=b"\x01" * 32, key_history={1: b"\x01" * 32})
    assert realm.verify_key(b"\xff" * 32, 1) is False


def test_verify_key_rejects_version_outside_grace_window(tmp_path) -> None:
    realm = Realm(key_version=1, current_key=b"\x01" * 32, key_history={1: b"\x01" * 32})
    for _ in range(5):  # rotate past the grace window (default N=3)
        realm.rotate_key()

    assert realm.verify_key(b"\x01" * 32, 1) is False


def test_key_for_version_returns_none_for_unknown_version() -> None:
    realm = Realm(key_version=1, current_key=b"\x01" * 32, key_history={1: b"\x01" * 32})
    assert realm.key_for_version(99) is None
