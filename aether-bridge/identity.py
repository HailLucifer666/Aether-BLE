"""Node identity: Ed25519 keypair generation/persistence and node_id derivation.

Persists to a flat JSON file (default `~/.aether/identity.json`), matching the
project's zero-infra/no-database philosophy. node_id is a stable fingerprint
of the public key (SHA-256, hex-encoded, truncated) - short enough for logs
and mDNS TXT records while remaining collision-resistant for this project's
scale (a handful of personal devices).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DEFAULT_IDENTITY_PATH = Path.home() / ".aether" / "identity.json"

NODE_ID_LENGTH = 16  # hex chars (64-bit fingerprint) - human-readable, still collision-safe for this fleet size


def node_id_from_public_key(public_key_bytes: bytes) -> str:
    """Derive a stable node_id fingerprint from raw Ed25519 public key bytes."""
    digest = hashlib.sha256(public_key_bytes).hexdigest()
    return digest[:NODE_ID_LENGTH]


@dataclass
class Identity:
    private_key: Ed25519PrivateKey
    node_id: str

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    @property
    def public_key_hex(self) -> str:
        return self.public_key.public_bytes_raw().hex()


def load_or_create_identity(path: Path | str = DEFAULT_IDENTITY_PATH) -> Identity:
    """Load this node's Ed25519 identity from disk, generating one on first run.

    The private key is stored raw-hex in the JSON file. There is no
    passphrase/encryption-at-rest for the key file this phase - it relies on
    OS filesystem permissions, consistent with this phase's LAN-trust
    posture (see PROTOCOL.md Security Annex).
    """
    path = Path(path)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data["private_key_hex"]))
        node_id = data["node_id"]
        return Identity(private_key=private_key, node_id=node_id)

    private_key = Ed25519PrivateKey.generate()
    public_key_bytes = private_key.public_key().public_bytes_raw()
    node_id = node_id_from_public_key(public_key_bytes)

    path.parent.mkdir(parents=True, exist_ok=True)
    private_key_bytes = private_key.private_bytes_raw()
    path.write_text(
        json.dumps({"private_key_hex": private_key_bytes.hex(), "node_id": node_id}),
        encoding="utf-8",
    )
    return Identity(private_key=private_key, node_id=node_id)
