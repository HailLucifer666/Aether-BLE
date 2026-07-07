"""QR pairing ceremony: offering side only.

Generates a pairing payload `{pubkey, mdns_name, realm_invite_token}`,
renders it as a QR code (ASCII, terminal-friendly - no Pillow dependency
required), and runs a short-lived local TCP listener that performs mutual
Ed25519 key exchange with a connecting peer.

The scanning side (an Android app decoding the QR and dialing in) is a later
phase; this module only needs to make the offering side work and be
testable by driving the socket directly, per this phase's scope.
"""

from __future__ import annotations

import asyncio
import io
import json

import qrcode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def build_pairing_payload(pubkey_hex: str, mdns_name: str, realm_invite_token: str) -> str:
    """Build the JSON payload encoded into the pairing QR code."""
    return json.dumps(
        {"pubkey": pubkey_hex, "mdns_name": mdns_name, "realm_invite_token": realm_invite_token}
    )


def render_pairing_qr_ascii(payload: str) -> str:
    """Render `payload` as an ASCII-art QR code (no Pillow/PNG dependency).

    Written to an in-memory buffer rather than stdout directly, so callers
    control encoding (Windows consoles default to a legacy codepage that
    cannot render the QR's block characters) and so this is testable.
    """
    qr = qrcode.QRCode()
    qr.add_data(payload)
    qr.make()
    buffer = io.StringIO()
    qr.print_ascii(out=buffer)
    return buffer.getvalue()


class PairingCeremony:
    """Local TCP listener performing mutual Ed25519 key exchange with a peer.

    Protocol (newline-delimited JSON, one exchange per connection):
      peer -> server: {"pubkey": "<hex>", "realm_invite_token": "<token>"}
      server -> peer: {"pubkey": "<hex>"}                    on success
                       {"error": "invalid_invite_token"}      on failure

    On success the peer's pubkey is recorded in `admitted_pubkeys`; the
    caller (bridge/realm setup) is responsible for actually adding the peer
    to the realm's member list.
    """

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        realm_invite_token: str,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.private_key = private_key
        self.realm_invite_token = realm_invite_token
        self.host = host
        self._requested_port = port
        self._server: asyncio.base_events.Server | None = None
        self.admitted_pubkeys: list[str] = []

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("PairingCeremony has not been started")
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_peer, self.host, self._requested_port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_peer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            request = json.loads(line.decode("utf-8"))
            peer_pubkey_hex = request.get("pubkey", "")
            token = request.get("realm_invite_token", "")

            if token != self.realm_invite_token or not peer_pubkey_hex:
                writer.write((json.dumps({"error": "invalid_invite_token"}) + "\n").encode())
                await writer.drain()
                return

            self.admitted_pubkeys.append(peer_pubkey_hex)
            own_pubkey_hex = self.private_key.public_key().public_bytes_raw().hex()
            writer.write((json.dumps({"pubkey": own_pubkey_hex}) + "\n").encode())
            await writer.drain()
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            writer.write((json.dumps({"error": "malformed_request"}) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
