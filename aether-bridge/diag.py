"""Aether Protocol Phase 1 - BLE diagnostic gate.

Separates two independent failure modes so debugging time isn't wasted
conflating them:

  1a ("radio")  - Is Windows BLE scanning working at all on this machine?
  1b ("name")   - Given a working radio, is our target beacon advertising
                  under the expected local name?

Matching is ALWAYS done by advertised local name, never by MAC address:
Android rotates the BLE MAC per advertising session, so MAC matching is
fundamentally unreliable for this beacon.
"""

import argparse
import asyncio
import sys
from datetime import datetime

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

DEFAULT_TARGET_NAME = "OnePlus 7T"
DEFAULT_RADIO_WINDOW_SECONDS = 10.0


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _raw_name(device: BLEDevice, adv: AdvertisementData) -> str | None:
    """Return whichever of local_name / device.name is populated.

    We log the exact raw string seen (whatever its casing) so an operator
    can confirm what nRF Connect is actually broadcasting.
    """
    if adv.local_name:
        return adv.local_name
    if device.name:
        return device.name
    return None


async def run_radio_mode(window_seconds: float) -> bool:
    """Step 1a: generic scan, no name filter. Returns True if radio works."""
    print(f"[radio] Scanning for any BLE advertisement for {window_seconds:.1f}s ...")
    print("[radio] (No name filter - this only tests that the radio/driver/stack works.)")

    seen: dict[str, tuple[BLEDevice, AdvertisementData]] = {}

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        if device.address not in seen:
            name = _raw_name(device, adv)
            print(
                f"[radio] {_now()} NEW  addr={device.address}  "
                f"name={name!r}  rssi={adv.rssi}"
            )
        seen[device.address] = (device, adv)

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    try:
        await asyncio.sleep(window_seconds)
    finally:
        await scanner.stop()

    print(f"[radio] Window complete. {len(seen)} distinct device(s) seen.")

    if not seen:
        print(
            "[radio] RESULT: FAIL - RADIO/DRIVER/PERMISSIONS ISSUE - "
            "no BLE advertisements of any kind detected. This is NOT a "
            "'beacon not found' problem; the Windows BLE stack itself saw "
            "nothing at all. Check Bluetooth is enabled, adapter drivers, "
            "and app Bluetooth permissions before doing anything else."
        )
        return False

    print("[radio] RESULT: PASS - the radio/driver/permissions stack is working.")
    return True


async def run_name_mode(target_name: str, radio_confirmed_ok: bool | None = None) -> None:
    """Step 1b: continuous scan filtered to a target local name.

    Prints a live, continuously-updating RSSI readout for the matched
    beacon so an operator moving the phone can watch numbers change.

    `radio_confirmed_ok` is None when this mode is run standalone (in which
    case we do not know the radio's state ahead of time and simply report
    what we saw). When running as part of `--mode both`, the caller passes
    the 1a result so we can give an unambiguous diagnosis if nothing matches.
    """
    target_lower = target_name.lower()
    other_addresses: set[str] = set()
    match_count = 0

    print(f"[name] Scanning continuously for local name == {target_name!r} (case-insensitive).")
    print("[name] Press Ctrl+C to stop.")

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        nonlocal match_count
        raw = _raw_name(device, adv)
        if raw is not None and raw.lower() == target_lower:
            match_count += 1
            print(
                f"\r[name] {_now()}  MATCH  addr={device.address}  "
                f"raw_name={raw!r}  rssi={adv.rssi:>4} dBm    ",
                end="",
                flush=True,
            )
        else:
            other_addresses.add(device.address)

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    try:
        while True:
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        await scanner.stop()
        print()  # move off the \r line

    if match_count == 0:
        if radio_confirmed_ok:
            print(
                f"[name] RESULT: NAME MATCH ISSUE - radio works "
                f"(saw {len(other_addresses)} other device(s)) but no device "
                f"advertised local name {target_name!r}. Check the phone's "
                f"screen is unlocked (advertising stops when the screen "
                f"locks) and that nRF Connect is still advertising."
            )
        else:
            print(
                f"[name] RESULT: no advertisement matching {target_name!r} seen "
                f"(saw {len(other_addresses)} other device(s) by name/address)."
            )
    else:
        print(f"[name] RESULT: PASS - matched {target_name!r} {match_count} time(s).")


async def run_name_mode_bounded(target_name: str, radio_confirmed_ok: bool) -> None:
    """Wrapper that lets Ctrl+C cancel run_name_mode cleanly when chained after 1a."""
    task = asyncio.ensure_future(run_name_mode(target_name, radio_confirmed_ok))
    try:
        await task
    except asyncio.CancelledError:
        task.cancel()
        raise


async def main_async(args: argparse.Namespace) -> None:
    if args.mode == "radio":
        await run_radio_mode(args.window)
        return

    if args.mode == "name":
        await run_name_mode(args.name, radio_confirmed_ok=None)
        return

    # mode == "both": run 1a first, then 1b. Refuse to trust 1b's result
    # (i.e. present it with proper context) if 1a saw zero advertisements.
    radio_ok = await run_radio_mode(args.window)
    print()
    if not radio_ok:
        print(
            "[both] Skipping name-match interpretation guarantees: radio saw "
            "nothing, so a failed name match below cannot be trusted as a "
            "'wrong name' diagnosis - fix the radio issue first."
        )
    await run_name_mode(args.name, radio_confirmed_ok=radio_ok)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aether Protocol BLE diagnostic gate (radio vs. name-match)."
    )
    parser.add_argument(
        "--mode",
        choices=["radio", "name", "both"],
        default="both",
        help="Which diagnostic step to run (default: both).",
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_TARGET_NAME,
        help=f"Target BLE local name to match in 'name' mode (default: {DEFAULT_TARGET_NAME!r}).",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=DEFAULT_RADIO_WINDOW_SECONDS,
        help=f"Radio scan window in seconds for 'radio' mode (default: {DEFAULT_RADIO_WINDOW_SECONDS}).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[diag] Stopped by user.")


if __name__ == "__main__":
    main()
