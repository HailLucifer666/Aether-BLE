"""mDNS peer discovery via zeroconf, for `_aether._tcp.local`.

Replaces a static, operator-supplied peer list (PROTOCOL.md §10 non-goal for
earlier phases) with automatic LAN discovery. A node registers itself once
at startup and can browse for peers at any time; no central directory.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

SERVICE_TYPE = "_aether._tcp.local."


def build_service_info(instance_name: str, node_id: str, port: int, address: str) -> ServiceInfo:
    """Build the ServiceInfo record advertised for this node.

    `node_id` is carried in the TXT record so a browser can identify peers
    without a separate handshake round-trip.
    """
    return ServiceInfo(
        type_=SERVICE_TYPE,
        name=f"{instance_name}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(address)],
        port=port,
        properties={"node_id": node_id},
        server=f"{instance_name}.local.",
    )


@dataclass
class _DiscoveredPeer:
    node_id: str
    address: str
    port: int


class _CollectingListener(ServiceListener):
    """Collects currently-known peers as zeroconf notifies of them."""

    def __init__(self, zeroconf: Zeroconf) -> None:
        self._zeroconf = zeroconf
        self.peers: dict[str, _DiscoveredPeer] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info is None or not info.addresses:
            return
        node_id = info.properties.get(b"node_id", b"").decode("utf-8", errors="replace")
        address = socket.inet_ntoa(info.addresses[0])
        self.peers[name] = _DiscoveredPeer(node_id=node_id, address=address, port=info.port)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.peers.pop(name, None)


class AetherDiscovery:
    """Registers this node and/or browses for peers on `_aether._tcp.local`."""

    def __init__(self) -> None:
        self._zeroconf = Zeroconf()
        self._registered_info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None
        self._listener: _CollectingListener | None = None

    def register(self, instance_name: str, node_id: str, port: int, address: str) -> None:
        """Advertise this node on the LAN. Call once at startup."""
        info = build_service_info(instance_name, node_id, port, address)
        self._zeroconf.register_service(info)
        self._registered_info = info

    def discover_peers(self, timeout_seconds: float = 2.0) -> list[dict]:
        """Browse for peers and return what's known after `timeout_seconds`.

        Starts a browser on first call and reuses it on subsequent calls
        (zeroconf keeps its listener's peer map updated continuously).
        """
        if self._browser is None:
            self._listener = _CollectingListener(self._zeroconf)
            self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, self._listener)

        time.sleep(timeout_seconds)
        assert self._listener is not None
        return [
            {"node_id": peer.node_id, "address": peer.address, "port": peer.port}
            for peer in self._listener.peers.values()
        ]

    def close(self) -> None:
        if self._registered_info is not None:
            self._zeroconf.unregister_service(self._registered_info)
        if self._browser is not None:
            self._browser.cancel()
        self._zeroconf.close()
