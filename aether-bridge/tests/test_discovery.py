"""Tests for discovery.py (zeroconf mDNS registration/browsing).

Uses zeroconf's real Zeroconf/ServiceInfo objects but talks to a fake
registry (no actual network I/O) by monkeypatching Zeroconf where the
project boundary allows - specifically, we test the pure payload-building
logic directly, and exercise register/unregister against a real local
Zeroconf instance (loopback-safe, no external network dependency) for an
integration-level smoke test.

Run with: pytest tests/test_discovery.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discovery import SERVICE_TYPE, build_service_info


def test_build_service_info_uses_correct_service_type() -> None:
    info = build_service_info(
        instance_name="aether-pc", node_id="abc123", port=8765, address="127.0.0.1"
    )
    assert info.type == SERVICE_TYPE
    assert info.name == f"aether-pc.{SERVICE_TYPE}"
    assert info.port == 8765


def test_build_service_info_includes_node_id_in_properties() -> None:
    info = build_service_info(
        instance_name="aether-pc", node_id="abc123", port=8765, address="127.0.0.1"
    )
    assert info.properties[b"node_id"] == b"abc123"


def test_register_and_browse_local_service_round_trip() -> None:
    """Integration smoke test: register a service via zeroconf and confirm
    a browser observes it. Uses loopback only - no external network needed.
    """
    import time

    from discovery import AetherDiscovery

    registrar = AetherDiscovery()
    browser = AetherDiscovery()
    try:
        registrar.register("aether-test-node", node_id="feedface", port=9999, address="127.0.0.1")

        found = []
        for _ in range(50):  # poll up to ~5s for mDNS propagation
            found = browser.discover_peers(timeout_seconds=0.1)
            if any(peer["node_id"] == "feedface" for peer in found):
                break
            time.sleep(0.1)

        assert any(peer["node_id"] == "feedface" for peer in found)
    finally:
        registrar.close()
        browser.close()
