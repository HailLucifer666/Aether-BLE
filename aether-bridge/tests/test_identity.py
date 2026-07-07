"""Tests for identity.py (Ed25519 keypair gen/load, node_id fingerprint).

Run with: pytest tests/test_identity.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from identity import Identity, load_or_create_identity, node_id_from_public_key


def test_load_or_create_identity_creates_new_keypair(tmp_path) -> None:
    identity_path = tmp_path / "identity.json"
    identity = load_or_create_identity(identity_path)

    assert isinstance(identity, Identity)
    assert isinstance(identity.private_key, Ed25519PrivateKey)
    assert len(identity.node_id) > 0
    assert identity_path.exists()


def test_load_or_create_identity_persists_and_reloads_same_keypair(tmp_path) -> None:
    identity_path = tmp_path / "identity.json"
    first = load_or_create_identity(identity_path)
    second = load_or_create_identity(identity_path)

    assert first.node_id == second.node_id
    first_pub = first.private_key.public_key().public_bytes_raw()
    second_pub = second.private_key.public_key().public_bytes_raw()
    assert first_pub == second_pub


def test_node_id_is_deterministic_fingerprint_of_public_key() -> None:
    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes_raw()

    node_id_1 = node_id_from_public_key(pub_bytes)
    node_id_2 = node_id_from_public_key(pub_bytes)

    assert node_id_1 == node_id_2
    assert isinstance(node_id_1, str)


def test_different_keys_produce_different_node_ids() -> None:
    key_a = Ed25519PrivateKey.generate()
    key_b = Ed25519PrivateKey.generate()

    node_id_a = node_id_from_public_key(key_a.public_key().public_bytes_raw())
    node_id_b = node_id_from_public_key(key_b.public_key().public_bytes_raw())

    assert node_id_a != node_id_b


def test_load_or_create_identity_creates_parent_directory(tmp_path) -> None:
    identity_path = tmp_path / "nested" / "dir" / "identity.json"
    identity = load_or_create_identity(identity_path)

    assert identity_path.exists()
    assert identity.node_id
