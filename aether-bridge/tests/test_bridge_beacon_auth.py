"""Tests for bridge.py's authenticated-beacon verification path (Phase 6).

Covers the replacement of plaintext name matching with
beacon_auth.verify_beacon() in Bridge._on_advertisement, while preserving
the existing EMA/BeaconState/build_reading_message flow on success and
silent (debug-log-only) drop on failure/replay.

_on_advertisement calls asyncio.get_running_loop() (pre-existing behavior,
unchanged by this phase), so every test drives it inside asyncio.run().

Run with: pytest tests/test_bridge_beacon_auth.py -v
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beacon_auth import BeaconCounterStore, build_beacon_payload, uid_hash_from_name
from bridge import Bridge

REALM_KEY = b"\x7a" * 32
TARGET_NAME = "OnePlus 7T"


def _make_bridge(tmp_path) -> Bridge:
    counter_store = BeaconCounterStore(tmp_path / "beacon_counter.json")
    return Bridge(
        target_name=TARGET_NAME,
        scanner_id="PC",
        host="127.0.0.1",
        port=0,
        realm_key=REALM_KEY,
        counter_store=counter_store,
    )


def _adv_with_payload(payload: bytes) -> MagicMock:
    adv = MagicMock()
    adv.local_name = None
    adv.manufacturer_data = {0xFFFF: payload}
    adv.rssi = -55
    return adv


def _run(coro) -> None:
    asyncio.run(coro)


def test_valid_beacon_updates_state(tmp_path) -> None:
    async def body():
        bridge = _make_bridge(tmp_path)
        uid_hash = uid_hash_from_name(TARGET_NAME)
        payload = build_beacon_payload(REALM_KEY, uid_hash, counter=1)

        device = MagicMock()
        device.name = None
        bridge._on_advertisement(device, _adv_with_payload(payload))

        assert bridge.state.raw_rssi == -55.0
        assert bridge.state.is_lost is False

    _run(body())


def test_replayed_counter_does_not_update_state(tmp_path) -> None:
    async def body():
        bridge = _make_bridge(tmp_path)
        uid_hash = uid_hash_from_name(TARGET_NAME)
        payload = build_beacon_payload(REALM_KEY, uid_hash, counter=5)

        device = MagicMock()
        device.name = None
        bridge._on_advertisement(device, _adv_with_payload(payload))
        assert bridge.state.raw_rssi == -55.0

        # Replay the exact same payload (same counter) - must be rejected.
        bridge.state.raw_rssi = None
        bridge._on_advertisement(device, _adv_with_payload(payload))
        assert bridge.state.raw_rssi is None

    _run(body())


def test_wrong_realm_key_does_not_update_state(tmp_path) -> None:
    async def body():
        bridge = _make_bridge(tmp_path)
        uid_hash = uid_hash_from_name(TARGET_NAME)
        payload = build_beacon_payload(b"\x00" * 32, uid_hash, counter=1)  # wrong key

        device = MagicMock()
        device.name = None
        bridge._on_advertisement(device, _adv_with_payload(payload))

        assert bridge.state.raw_rssi is None

    _run(body())


def test_missing_manufacturer_data_does_not_update_state(tmp_path) -> None:
    async def body():
        bridge = _make_bridge(tmp_path)
        device = MagicMock()
        device.name = None
        adv = MagicMock()
        adv.local_name = None
        adv.manufacturer_data = {}
        adv.rssi = -55

        bridge._on_advertisement(device, adv)
        assert bridge.state.raw_rssi is None

    _run(body())


def test_grace_window_key_history_accepts_beacon_signed_with_old_key(tmp_path) -> None:
    """A beacon signed with a key that has since been rotated out of
    `realm_key` must still verify as long as that key is present in
    `key_history` (the grace window) - see realm.py / PROTOCOL.md Security
    Annex. Without this, every node still on the previous key would be
    instantly locked out the moment a realm key rotation ships.
    """

    async def body():
        old_key = REALM_KEY
        new_key = b"\x99" * 32
        counter_store = BeaconCounterStore(tmp_path / "beacon_counter.json")
        bridge = Bridge(
            target_name=TARGET_NAME,
            scanner_id="PC",
            host="127.0.0.1",
            port=0,
            realm_key=new_key,
            counter_store=counter_store,
            key_history=[new_key, old_key],
        )
        uid_hash = uid_hash_from_name(TARGET_NAME)
        # Beacon still signed with the pre-rotation key - must still verify.
        payload = build_beacon_payload(old_key, uid_hash, counter=1)

        device = MagicMock()
        device.name = None
        bridge._on_advertisement(device, _adv_with_payload(payload))

        assert bridge.state.raw_rssi == -55.0
        assert bridge.state.is_lost is False

    _run(body())


def test_key_not_in_history_is_rejected(tmp_path) -> None:
    """Sanity check on the other side of the grace window: a key that was
    never in candidate_keys must not verify, proving the fix isn't a
    trivial accept-anything regression.
    """

    async def body():
        counter_store = BeaconCounterStore(tmp_path / "beacon_counter.json")
        bridge = Bridge(
            target_name=TARGET_NAME,
            scanner_id="PC",
            host="127.0.0.1",
            port=0,
            realm_key=REALM_KEY,
            counter_store=counter_store,
            key_history=[REALM_KEY],  # pruned - old key not present
        )
        uid_hash = uid_hash_from_name(TARGET_NAME)
        payload = build_beacon_payload(b"\x11" * 32, uid_hash, counter=1)  # not in history

        device = MagicMock()
        device.name = None
        bridge._on_advertisement(device, _adv_with_payload(payload))

        assert bridge.state.raw_rssi is None

    _run(body())


def test_advertisement_heartbeat_updates_even_on_verification_failure(tmp_path) -> None:
    """Any-advertisement stall watchdog must still see this callback fire,
    even when the beacon payload itself is rejected.
    """

    async def body():
        bridge = _make_bridge(tmp_path)
        device = MagicMock()
        device.name = None
        adv = MagicMock()
        adv.local_name = None
        adv.manufacturer_data = {}
        adv.rssi = -55
        bridge._on_advertisement(device, adv)
        assert bridge._last_any_advertisement_monotonic is not None

    _run(body())
