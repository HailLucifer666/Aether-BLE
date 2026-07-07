"""Tests for messages.build_hello_message (Phase 6 addition).

Run with: pytest tests/test_messages_hello.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from messages import build_hello_message


def test_build_hello_message_schema() -> None:
    msg = build_hello_message(
        ver=1, node_id="abc123", capabilities=["beacon", "ranging"], sig="deadbeef"
    )

    assert msg["type"] == "hello"
    assert msg["ver"] == 1
    assert msg["node_id"] == "abc123"
    assert msg["capabilities"] == ["beacon", "ranging"]
    assert msg["sig"] == "deadbeef"
    assert "ts" in msg


def test_build_hello_message_empty_capabilities() -> None:
    msg = build_hello_message(ver=1, node_id="n1", capabilities=[], sig="")
    assert msg["capabilities"] == []
