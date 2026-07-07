"""Tests for llm.py's Ollama HTTP client.

The real-call test drives the ACTUALLY-RUNNING local Ollama instance on this
machine (not mocked, per this project's Prime Directive) and skips cleanly
with a clear reason if Ollama isn't reachable, rather than failing the whole
suite - per docs/phase8/PRD.md's acceptance criterion for llm.py.

Run with: pytest tests/test_llm.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm


def _ollama_reachable() -> bool:
    import requests

    try:
        requests.get(f"{llm.DEFAULT_OLLAMA_URL}/api/tags", timeout=1.0)
        return True
    except requests.RequestException:
        return False


def test_build_prompt_includes_transcript_and_new_text():
    prompt = llm._build_prompt(
        [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello there"}],
        "what time is it",
    )
    assert "User: hi" in prompt
    assert "Assistant: hello there" in prompt
    assert "User: what time is it" in prompt
    assert prompt.strip().endswith("Assistant:")


def test_build_prompt_with_empty_context():
    prompt = llm._build_prompt([], "hello")
    assert prompt == "User: hello\nAssistant:"


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama instance not reachable at http://localhost:11434")
def test_generate_reply_real_ollama_call():
    """Real, non-mocked call against the actually-running Ollama instance."""
    reply = llm.generate_reply([], "Reply with exactly the word: PONG")
    assert isinstance(reply, str)
    assert len(reply.strip()) > 0


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama instance not reachable at http://localhost:11434")
def test_generate_reply_uses_transcript_context_real_call():
    """A real call with prior transcript context still returns usable text -
    doesn't assert on exact model output (that would be flaky), only on the
    contract (non-empty string)."""
    context = [
        {"role": "user", "text": "My name is Aether."},
        {"role": "assistant", "text": "Nice to meet you, Aether."},
    ]
    reply = llm.generate_reply(context, "What is my name?")
    assert isinstance(reply, str)
    assert len(reply.strip()) > 0


def test_generate_reply_raises_llmerror_on_unreachable_host():
    """Pointing at a definitely-unreachable port must raise LLMError, not
    hang or raise a raw requests exception - callers rely on catching
    exactly LLMError to trigger their fallback (see aggregator._handle_ask)."""
    with pytest.raises(llm.LLMError):
        llm.generate_reply([], "hello", base_url="http://127.0.0.1:1", timeout_seconds=1.0)
