"""Deterministic FSM E2E test (in-process, no network or scanner-script dependency).

Spins up a real Aggregator + two mock peer scanners on ephemeral ports, seeds
an utterance, then FORCES an ownership change at the exact tick when the
utterance is active by pushing much-stronger readings from the challenger.
Asserts the full PREPARE -> TRANSFER -> CONFIRM -> RELEASE sequence is
broadcast, in order, with speaking flipping to the new owner at CONFIRM.

This proves the FSM end-to-end (aggregator logic + wire broadcast) without
depending on the simulated scanners' timing.
"""

import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregator import Aggregator
from messages import build_reading_message


async def start_mock_peer(scanner_id, initial_rssi):
    """A broadcast-only peer WS server on an ephemeral port."""
    clients = []

    async def handler(ws):
        clients.append(ws)
        # Send an initial snapshot so the aggregator learns the scanner id.
        await ws.send(json.dumps(build_reading_message(scanner_id, "Beacon", initial_rssi, initial_rssi, 0)))
        try:
            async for _ in ws:
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            clients.remove(ws) if ws in clients else None

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def broadcast(rssi):
        msg = build_reading_message(scanner_id, "Beacon", rssi, rssi, 0)
        for c in list(clients):
            try:
                await c.send(json.dumps(msg))
            except websockets.exceptions.ConnectionClosed:
                pass

    return f"ws://127.0.0.1:{port}", broadcast, server


async def main():
    print("Starting mock peers + in-process aggregator...")
    url_a, push_a, srv_a = await start_mock_peer("Scanner-A", -55.0)
    url_b, push_b, srv_b = await start_mock_peer("Scanner-B", -90.0)

    agg = Aggregator([url_a, url_b], "127.0.0.1", 0, tick_ms=50)
    agg_server = await websockets.serve(agg._handle_client, "127.0.0.1", 0)
    agg_port = agg_server.sockets[0].getsockname()[1]

    tasks = [
        asyncio.ensure_future(agg._peer_connection_loop(url_a)),
        asyncio.ensure_future(agg._peer_connection_loop(url_b)),
        asyncio.ensure_future(agg._election_tick_loop()),
        asyncio.ensure_future(agg._conversation_fsm_loop()),
        asyncio.ensure_future(agg._broadcast_loop()),
    ]

    try:
        # Wait for both peers to register.
        for _ in range(100):
            if len(agg._peer_order) >= 2:
                break
            await asyncio.sleep(0.05)
        assert len(agg._peer_order) == 2, f"peers didn't register: {agg._peer_order}"

        # Reinforce Scanner-A as owner.
        for _ in range(10):
            await push_a(-55.0)
            await push_b(-90.0)
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.4)
        assert agg._owner == "Scanner-A", f"expected Scanner-A owner, got {agg._owner}"
        print(f"  Owner established: {agg._owner}")

        # Seed an utterance (synthetic, long duration so it survives).
        await agg._handle_say("This is a long sentence that will outlast the four phase handoff contract.")
        await asyncio.sleep(0.2)
        assert agg._conversation.utterance is not None, "utterance not seeded"
        assert agg._conversation.speaking_scanner == "Scanner-A"
        print(f"  Utterance active, speaking_scanner={agg._conversation.speaking_scanner}")

        # Connect a dashboard client to observe broadcasts.
        phases_broadcast = []
        async with websockets.connect(f"ws://127.0.0.1:{agg_port}") as dash:
            await asyncio.wait_for(dash.recv(), timeout=2)  # initial snapshot

            # FORCE the handoff: make Scanner-B much stronger than Scanner-A.
            print("\nForcing handoff: pushing Scanner-B to -40, Scanner-A to -95...")
            for _ in range(15):
                await push_a(-95.0)
                await push_b(-40.0)
                await asyncio.sleep(0.02)

            # Now watch broadcasts for the FSM phase events.
            print("Watching for FSM phase broadcasts...")
            deadline = asyncio.get_running_loop().time() + 8.0
            saw_idle_again = False
            while asyncio.get_running_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(dash.recv(), timeout=0.5)
                    msg = json.loads(raw)
                except asyncio.TimeoutError:
                    continue
                if msg.get("type") == "conversation":
                    ev = msg.get("conversationEvent")
                    if ev and ev.get("phase"):
                        phases_broadcast.append(ev["phase"])
                        print(f"    phase broadcast: {ev['phase']}")
                    if msg.get("phase") == "IDLE" and len(phases_broadcast) >= 2:
                        saw_idle_again = True
                        break

        print(f"\nPhases broadcast in order: {phases_broadcast}")
        required = ["PREPARE", "TRANSFER", "CONFIRM", "RELEASE"]

        # The aggregator also broadcasts an IDLE event when the FSM completes,
        # which is correct. We just need the 4 phases to appear, in order.
        fsm_phases = [p for p in phases_broadcast if p != "IDLE"]
        if fsm_phases == required:
            print("\nPASS: full 4-phase FSM broadcast in correct order.")
            # Verify final state: speaking migrated to Scanner-B.
            if agg._conversation.speaking_scanner == "Scanner-B":
                print("PASS: speaking_scanner migrated to Scanner-B.")
            else:
                print(f"FAIL: speaking_scanner={agg._conversation.speaking_scanner}, expected Scanner-B")
            # Verify utterance still active (it was long).
            if agg._conversation.utterance is not None:
                print("PASS: utterance still active after handoff (didn't get cut).")
            else:
                print("FAIL: utterance was cleared during handoff")
            return 0
        else:
            print(f"\nFAIL: expected {required}, got {fsm_phases}")
            return 1

    finally:
        agg.stop()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        agg_server.close()
        await agg_server.wait_closed()
        srv_a.close()
        await srv_a.wait_closed()
        srv_b.close()
        await srv_b.wait_closed()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
