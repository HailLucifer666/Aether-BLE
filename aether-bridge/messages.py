"""Wire-format message builders shared by bridge, simulated scanner, and aggregator.

These functions define the JSON contract sent to WebSocket clients (dashboard).
Field names here are a locked contract - do not rename fields without updating
every consumer.
"""

from datetime import datetime


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def build_reading_message(
    scanner: str, name: str, raw_rssi: float, smoothed_rssi: float, last_seen_ms: int
) -> dict:
    return {
        "type": "reading",
        "scanner": scanner,
        "name": name,
        "rssi": raw_rssi,
        "smoothedRssi": smoothed_rssi,
        "lastSeenMs": last_seen_ms,
        "ts": _now_hms(),
    }


def build_lost_message(scanner: str, name: str) -> dict:
    return {
        "type": "lost",
        "scanner": scanner,
        "name": name,
        "ts": _now_hms(),
    }


def build_election_message(
    owner: str | None,
    tick: int,
    scanners: list[dict],
    last_handoff: dict | None,
    wake_outcome: dict | None,
) -> dict:
    """Build the election broadcast message.

    Locked schema (dashboard consumes this directly):
        {"type": "election", "owner": "PC", "tick": 4831, "ts": "14:32:41",
         "scanners": [{"id": "PC", "rssi": -58.2, "smoothedRssi": -59.1,
                        "lastSeenMs": 340, "present": true}, ...],
         "lastHandoff": {"from": "SIM-A", "to": "PC", "atTick": 4821,
                          "ts": "14:32:15"},
         "wakeOutcome": {"requestedAtTick": 4830, "ts": "14:32:41",
                          "owner": "PC",
                          "results": [{"id": "PC", "outcome": "ACCEPTED"},
                                      {"id": "SIM-A", "outcome": "SUPPRESSED"}]}}

    `scanners` must be supplied in stable peers-list order, one entry per
    configured peer regardless of liveness. `last_handoff` and `wake_outcome`
    are passed through as-is (callers control the one-shot / most-recent-only
    semantics); this function performs no logic beyond envelope assembly.
    """
    return {
        "type": "election",
        "owner": owner,
        "tick": tick,
        "ts": _now_hms(),
        "scanners": scanners,
        "lastHandoff": last_handoff,
        "wakeOutcome": wake_outcome,
    }


def build_conversation_message(
    transcript: list[dict],
    utterance: dict | None,
    speaking_scanner: str | None,
    phase: str,
    phase_from: str | None,
    phase_to: str | None,
    conversation_event: dict | None,
) -> dict:
    """Build the conversation broadcast message.

    Sent as a separate JSON message on the same WebSocket as the election
    broadcast (immediately after it). Locked schema (dashboard consumes this
    directly):

        {"type": "conversation",
         "transcript": [{"id": 1, "scanner": "SIM-A", "role": "assistant",
                         "text": "...", "ts": "14:32:41"}, ...],
         "utterance": {"text": "...", "audioBase64": "data:audio/mp3;base64,...",
                       "durationMs": 2400, "offsetMs": 0,
                       "isSynthetic": false} | null,
         "speakingScanner": "SIM-A" | null,
         "phase": "IDLE" | "PREPARE" | "TRANSFER" | "CONFIRM" | "RELEASE",
         "phaseFrom": "SIM-A" | null,
         "phaseTo": "SIM-B" | null,
         "conversationEvent": {"phase": "PREPARE", "fromScanner": "SIM-A",
                               "toScanner": "SIM-B", "atTick": 4830} | null}

    `conversationEvent` is one-shot (caller clears it after one broadcast,
    mirroring wakeOutcome's semantics) and marks a phase transition for the
    dashboard to react to (e.g. pause audio on TRANSFER, resume on RELEASE).
    """
    return {
        "type": "conversation",
        "transcript": transcript,
        "utterance": utterance,
        "speakingScanner": speaking_scanner,
        "phase": phase,
        "phaseFrom": phase_from,
        "phaseTo": phase_to,
        "conversationEvent": conversation_event,
    }


def build_ranging_message(
    contest: dict | None,
    chirp: dict | None,
    fusion_reason: str,
    ranging_event: dict | None,
) -> dict:
    """Build the tier-2 ranging broadcast message (Phase 4).

    Sent as a separate JSON message on the same WebSocket as the election
    and conversation broadcasts. Additive schema - the existing election and
    conversation contracts are untouched. The dashboard dispatches inbound
    messages by `type`, so a new "ranging" type slots in without affecting
    prior consumers:

        {"type": "ranging",
         "contest": {"incumbentId": "SIM-A", "challengerId": "SIM-B",
                     "incumbentRssi": -60.0, "challengerRssi": -58.0,
                     "atTick": 4830} | null,
         "chirp": {"measurements": [{"scannerId": "SIM-A", "tofUs": 2000.0,
                                     "distanceM": 0.686},
                                    {"scannerId": "SIM-B", "tofUs": 1000.0,
                                     "distanceM": 0.343}],
                   "winnerId": "SIM-B", "sameRoom": true,
                   "resolvedTick": 4831} | null,
         "fusionReason": "ble-only" | "chirp-confirmed" | "chirp-resolved-tie"
                       | "chirp-room-containment",
         "rangingEvent": {"phase": "CHIRP", "contestIncumbent": "SIM-A",
                          "contestChallenger": "SIM-B", "winnerId": "SIM-B",
                          "sameRoom": true, "atTick": 4831} | null}

    `contest` is the current (most-recent) contest state, or null when the
    election is not contested. `chirp` is the most-recent chirp resolution
    still considered relevant (null before the first chirp). `fusionReason`
    is the machine-readable tag from ranging.FusionResult.reason describing
    how the latest owner decision was reached. `rangingEvent` is one-shot
    (caller clears it after one broadcast, mirroring wakeOutcome /
    conversationEvent semantics) and marks a chirp round the dashboard should
    react to with the "chirp ping" animation.
    """
    return {
        "type": "ranging",
        "contest": contest,
        "chirp": chirp,
        "fusionReason": fusion_reason,
        "rangingEvent": ranging_event,
    }
