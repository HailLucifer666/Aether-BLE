"""Tests for pairing.py (QR pairing ceremony - offering side).

Scanning side (Android app) is out of scope this phase; these tests cover
payload generation, QR rendering, and the local handshake listener's mutual
Ed25519 key exchange by driving both ends of the socket ourselves.

Run with: pytest tests/test_pairing.py -v
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pairing import (
    PairingCeremony,
    build_pairing_payload,
    render_pairing_qr_ascii,
)


def test_build_pairing_payload_contains_required_fields() -> None:
    payload = build_pairing_payload(
        pubkey_hex="ab" * 32, mdns_name="aether-pc", realm_invite_token="token123"
    )
    data = json.loads(payload)
    assert data["pubkey"] == "ab" * 32
    assert data["mdns_name"] == "aether-pc"
    assert data["realm_invite_token"] == "token123"


def test_render_pairing_qr_ascii_returns_nonempty_string() -> None:
    payload = build_pairing_payload("ab" * 32, "aether-pc", "token123")
    ascii_art = render_pairing_qr_ascii(payload)
    assert isinstance(ascii_art, str)
    assert len(ascii_art) > 0


def test_pairing_ceremony_mutual_key_exchange() -> None:
    """Drives a full handshake: ceremony listens, a simulated peer connects
    and exchanges public keys, ceremony admits the peer's key.
    """

    async def run_test() -> None:
        server_key = Ed25519PrivateKey.generate()
        ceremony = PairingCeremony(
            private_key=server_key,
            realm_invite_token="expected-token",
            host="127.0.0.1",
            port=0,
        )
        await ceremony.start()
        actual_port = ceremony.port

        peer_key = Ed25519PrivateKey.generate()
        peer_pub_hex = peer_key.public_key().public_bytes_raw().hex()

        reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
        writer.write(
            (json.dumps({"pubkey": peer_pub_hex, "realm_invite_token": "expected-token"}) + "\n").encode()
        )
        await writer.drain()

        response_line = await reader.readline()
        response = json.loads(response_line.decode())
        writer.close()
        await writer.wait_closed()
        await ceremony.stop()

        assert response["pubkey"] == server_key.public_key().public_bytes_raw().hex()
        assert peer_pub_hex in ceremony.admitted_pubkeys

    asyncio.run(run_test())


def test_pairing_ceremony_rejects_wrong_invite_token() -> None:
    async def run_test() -> None:
        server_key = Ed25519PrivateKey.generate()
        ceremony = PairingCeremony(
            private_key=server_key,
            realm_invite_token="expected-token",
            host="127.0.0.1",
            port=0,
        )
        await ceremony.start()
        actual_port = ceremony.port

        peer_key = Ed25519PrivateKey.generate()
        peer_pub_hex = peer_key.public_key().public_bytes_raw().hex()

        reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
        writer.write(
            (json.dumps({"pubkey": peer_pub_hex, "realm_invite_token": "wrong-token"}) + "\n").encode()
        )
        await writer.drain()

        response_line = await reader.readline()
        response = json.loads(response_line.decode())
        writer.close()
        await writer.wait_closed()
        await ceremony.stop()

        assert "error" in response
        assert peer_pub_hex not in ceremony.admitted_pubkeys

    asyncio.run(run_test())
