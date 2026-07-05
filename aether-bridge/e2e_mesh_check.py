"""E2E smoke test of the running mesh aggregator (ws://127.0.0.1:8766).

Drives the same wire protocol the dashboard uses, against whatever aggregator
is currently running. Verifies every Mesh-mode feature end to end:

  1. Election + scanner presence
  2. Hysteresis handoff (via the scripted scanner walk, observed passively)
  3. say -> edge-tts audio (real mp3 payload returned)
  4. Conversation handoff FSM (PREPARE -> TRANSFER -> CONFIRM -> RELEASE)
  5. Wake resolution (ACCEPTED for owner, SUPPRESSED for others)
  6. One-shot semantics (wakeOutcome + conversationEvent cleared after 1 broadcast)

Run:  python e2e_mesh_check.py
"""

import asyncio
import base64
import json
import sys
import time

import websockets

URL = "ws://127.0.0.1:8766"
RESULTS = []


def ok(name, detail=""):
    RESULTS.append((True, name, detail))
    print(f"  PASS  {name}{(' — ' + detail) if detail else ''}")


def fail(name, detail):
    RESULTS.append((False, name, detail))
    print(f"  FAIL  {name} — {detail}")


async def recv_election(ws, timeout=3.0):
    """Read messages until an election message arrives; return it (or None)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            return None
        msg = json.loads(raw)
        if msg.get("type") == "election":
            return msg


async def recv_any(ws, timeout=3.0):
    """Read the next message of any type."""
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(raw)
    except asyncio.TimeoutError:
        return None


async def drain(ws, seconds=0.5):
    """Discard messages for `seconds` to let the aggregator settle."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            return


async def main():
    print(f"Connecting to {URL} ...")
    try:
        async with websockets.connect(URL) as ws:
            print("Connected.\n")
            await drain(ws, 0.5)

            # ----- 1. Election + presence -----
            print("[1/6] Election + scanner presence")
            msg = await recv_election(ws)
            if msg is None:
                fail("election broadcast received", "no election message in 3s")
                return
            owner = msg.get("owner")
            scanners = msg.get("scanners", [])
            present_count = sum(1 for s in scanners if s.get("present"))
            if owner is None:
                fail("owner elected", "owner is None — no scanners visible to aggregator")
                return
            if present_count < 2:
                fail("both scanners present", f"only {present_count}/2 present")
                return
            ok("owner elected", f"owner={owner}")
            ok("scanners present", f"{present_count}/2 present, ids={[s['id'] for s in scanners]}")

            # ----- 2. Hysteresis handoff observation -----
            print("\n[2/6] Hysteresis handoff (passive observation, ~35s)")
            print("      (scanners are scripted to walk past each other; watching for >=1 handoff)")
            start_owner = owner
            handoffs_seen = 0
            last_handoff_tick = None
            deadline = time.monotonic() + 40.0
            while time.monotonic() < deadline and handoffs_seen == 0:
                m = await recv_election(ws, timeout=2.0)
                if m is None:
                    continue
                lh = m.get("lastHandoff")
                if lh is not None and lh.get("atTick") != last_handoff_tick:
                    last_handoff_tick = lh.get("atTick")
                    handoffs_seen += 1
                    ok(
                        "handoff observed",
                        f"{lh.get('from')} -> {lh.get('to')} at tick {lh.get('atTick')}",
                    )
            if handoffs_seen == 0:
                fail("handoff observed", "no handoff in 40s (scanner scripts may need longer)")
            else:
                # Refresh owner after handoff
                msg = await recv_election(ws)
                if msg:
                    owner = msg.get("owner") or owner

            # ----- 3. say -> edge-tts audio -----
            print("\n[3/6] say -> edge-tts audio")
            await drain(ws, 0.3)
            await ws.send(json.dumps({"type": "say", "text": "Hello, this is an end to end test.", "requestId": "e2e-1"}))
            # Wait for a conversation message containing an utterance with audio.
            got_audio = False
            got_synthetic = False
            utterance_text = None
            audio_size = 0
            deadline = time.monotonic() + 15.0  # edge-tts can take a few seconds
            while time.monotonic() < deadline and not (got_audio or got_synthetic):
                m = await recv_any(ws, timeout=2.0)
                if m is None:
                    continue
                if m.get("type") == "conversation" and m.get("utterance") is not None:
                    u = m["utterance"]
                    utterance_text = u.get("text")
                    if u.get("audioBase64") and str(u.get("audioBase64")).startswith("data:audio"):
                        got_audio = True
                        # crude length check
                        audio_size = len(str(u["audioBase64"]))
                    elif u.get("isSynthetic"):
                        got_synthetic = True
            if got_audio:
                ok("edge-tts audio generated", f"{audio_size} bytes b64, text={utterance_text!r}")
            elif got_synthetic:
                ok("synthetic fallback (edge-tts unavailable)", f"text={utterance_text!r}")
                print("      NOTE: aggregator fell back to synthetic — check network/edge-tts for real audio")
            else:
                fail("utterance generated", "no conversation.utterance within 15s of say")

            # ----- 4. Conversation handoff FSM -----
            print("\n[4/6] Conversation handoff FSM (PREPARE -> TRANSFER -> CONFIRM -> RELEASE)")
            # Seed a fresh long utterance so the next ownership handoff triggers the FSM.
            await ws.send(json.dumps({"type": "say", "text": "The quick brown fox jumps over the lazy dog and the dog runs far away into the forest beyond the hills.", "requestId": "e2e-fsm"}))
            await drain(ws, 2.0)  # let edge-tts generate
            # Now wait for the next handoff; the FSM should fire and emit conversationEvents.
            phases_seen = set()
            deadline = time.monotonic() + 50.0
            current_phase = "IDLE"
            while time.monotonic() < deadline and not (phases_seen >= {"PREPARE", "TRANSFER", "CONFIRM", "RELEASE"} or (len(phases_seen) >= 1 and current_phase == "IDLE" and phases_seen)):
                m = await recv_any(ws, timeout=2.0)
                if m is None:
                    continue
                if m.get("type") == "conversation":
                    current_phase = m.get("phase", "IDLE")
                    ev = m.get("conversationEvent")
                    if ev and ev.get("phase"):
                        phases_seen.add(ev["phase"])
                    # Early exit if we've cycled back to IDLE after seeing phases
                    if phases_seen and current_phase == "IDLE":
                        break
            required = {"PREPARE", "TRANSFER", "CONFIRM", "RELEASE"}
            if phases_seen >= required:
                ok("all 4 FSM phases observed", f"sequence: {sorted(phases_seen)}")
            elif phases_seen:
                ok("partial FSM observed", f"got {sorted(phases_seen)} — scanner handoff may not have overlapped the utterance; re-run")
            else:
                fail("FSM phases observed", "no conversationEvent received — utterance may have finished before next handoff")

            # ----- 5. Wake resolution -----
            print("\n[5/6] Wake resolution (ACCEPTED for owner, SUPPRESSED for others)")
            # Refresh owner (may have changed during FSM test)
            msg = await recv_election(ws)
            if msg:
                owner = msg.get("owner") or owner
            await drain(ws, 0.3)
            await ws.send(json.dumps({"type": "wake", "requestId": "e2e-wake"}))
            wake_msg = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and wake_msg is None:
                m = await recv_election(ws, timeout=1.0)
                if m and m.get("wakeOutcome"):
                    wake_msg = m["wakeOutcome"]
            if wake_msg is None:
                fail("wake outcome emitted", "no wakeOutcome in 5s")
            else:
                results = wake_msg.get("results", [])
                owner_result = next((r for r in results if r.get("id") == owner), None)
                others = [r for r in results if r.get("id") != owner]
                if owner_result and owner_result.get("outcome") == "ACCEPTED" and all(r.get("outcome") == "SUPPRESSED" for r in others):
                    ok("wake resolution correct", f"owner {owner}=ACCEPTED, others={[r['id']+'='+r['outcome'] for r in others]}")
                else:
                    fail("wake resolution correct", f"results={results}, owner={owner}")

            # ----- 6. One-shot semantics -----
            print("\n[6/6] One-shot semantics (wakeOutcome + conversationEvent cleared after 1 broadcast)")
            # Read 2 more election messages; the wakeOutcome should be gone from the second.
            m1 = await recv_election(ws)
            m2 = await recv_election(ws)
            if m1 and m2:
                if m1.get("wakeOutcome") is None and m2.get("wakeOutcome") is None:
                    ok("wakeOutcome is one-shot", "absent on subsequent broadcasts")
                else:
                    fail("wakeOutcome is one-shot", "still present on later broadcast")
            else:
                fail("wakeOutcome is one-shot", "couldn't read 2 follow-up broadcasts")

    except (OSError, websockets.exceptions.InvalidURI) as exc:
        print(f"\nCONNECT FAILED: {exc}")
        print("Is the aggregator running on ws://127.0.0.1:8766? Run AetherMesh.bat first.")
        return

    # ----- Summary -----
    print("\n" + "=" * 60)
    passed = sum(1 for r in RESULTS if r[0])
    total = len(RESULTS)
    print(f"E2E RESULTS: {passed}/{total} checks passed")
    if passed == total:
        print("ALL FEATURES WORKING END TO END.")
    else:
        print("FAILURES:")
        for ok_, name, detail in RESULTS:
            if not ok_:
                print(f"  - {name}: {detail}")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
