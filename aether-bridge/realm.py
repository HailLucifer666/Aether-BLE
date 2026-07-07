"""Realm key storage, versioning, and grace-window verification.

A "realm" is this mesh's shared secret admitting nodes to the home network.
Persists to a flat JSON file (default `~/.aether/realm.json`):
    {"realm_key_v": int, "realm_key": hex, "key_history": {"1": hex, ...},
     "members": [node_id, ...]}

Rotation keeps the last GRACE_WINDOW_VERSIONS keys verifiable so beacons/
messages signed just before a rotation aren't rejected mid-flight - the
"grace window" from the architecture doc. Older versions are pruned.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REALM_PATH = Path.home() / ".aether" / "realm.json"

REALM_KEY_LENGTH_BYTES = 32  # 256-bit key for HMAC-SHA256
GRACE_WINDOW_VERSIONS = 3  # how many most-recent key versions remain verifiable


@dataclass
class Realm:
    key_version: int
    current_key: bytes
    key_history: dict[int, bytes] = field(default_factory=dict)
    members: list[str] = field(default_factory=list)

    def key_for_version(self, version: int) -> bytes | None:
        return self.key_history.get(version)

    def verify_key(self, candidate_key: bytes, version: int) -> bool:
        """Check candidate_key matches the stored key for `version`, within
        the grace window (i.e. the version must still be in key_history).
        """
        stored = self.key_for_version(version)
        if stored is None:
            return False
        return candidate_key == stored

    def rotate_key(self) -> None:
        """Generate a new realm key, bump the version, prune old history
        beyond the grace window.
        """
        self.key_version += 1
        self.current_key = os.urandom(REALM_KEY_LENGTH_BYTES)
        self.key_history[self.key_version] = self.current_key

        oldest_allowed = self.key_version - GRACE_WINDOW_VERSIONS + 1
        for version in list(self.key_history):
            if version < oldest_allowed:
                del self.key_history[version]


def _realm_to_dict(realm: Realm) -> dict:
    return {
        "realm_key_v": realm.key_version,
        "realm_key": realm.current_key.hex(),
        "key_history": {str(v): k.hex() for v, k in realm.key_history.items()},
        "members": realm.members,
    }


def _realm_from_dict(data: dict) -> Realm:
    return Realm(
        key_version=data["realm_key_v"],
        current_key=bytes.fromhex(data["realm_key"]),
        key_history={int(v): bytes.fromhex(k) for v, k in data.get("key_history", {}).items()},
        members=data.get("members", []),
    )


def load_or_create_realm(path: Path | str = DEFAULT_REALM_PATH) -> Realm:
    """Load this node's realm key from disk, generating a new realm on first run."""
    path = Path(path)
    if path.exists():
        return _realm_from_dict(json.loads(path.read_text(encoding="utf-8")))

    key = os.urandom(REALM_KEY_LENGTH_BYTES)
    realm = Realm(key_version=1, current_key=key, key_history={1: key}, members=[])
    save_realm(realm, path)
    return realm


def save_realm(realm: Realm, path: Path | str = DEFAULT_REALM_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_realm_to_dict(realm)), encoding="utf-8")
