"""Aether Protocol Phase 8 - local LLM reply generation via Ollama.

Given transcript context (prior conversation.py TranscriptEntry-shaped dicts)
and new user text, calls the already-running local Ollama HTTP API and
returns generated reply text. No cloud call - Ollama is a local daemon the
user already has running (http://localhost:11434) with gemma3:1b-it-qat
(default, fast) and qwen3 (larger) pulled.

Zero asyncio/websockets imports - this module is a thin synchronous HTTP
client wrapper (matches election.py/conversation.py's discipline of keeping
I/O modules small and independently testable), driven by aggregator.py via
asyncio.to_thread since requests is a blocking call.
"""

import requests

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:1b-it-qat"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Fallback reply used when Ollama is unreachable or errors, so a WS "ask"
# message degrades gracefully instead of leaving the caller with nothing to
# speak - mirrors aggregator.py's edge-tts/Piper synthetic-fallback pattern.
FALLBACK_REPLY = "Sorry, I couldn't reach the local language model just now."


class LLMError(Exception):
    """Raised when the Ollama call fails; callers may catch this to fall back."""


def _build_prompt(transcript_context: list[dict], user_text: str) -> str:
    """Render prior transcript entries + new user text into a single prompt.

    `transcript_context` entries are shaped like conversation.TranscriptEntry
    (role: "user" | "assistant", text: str) - a plain dict is expected here so
    this module doesn't need to import conversation.py's dataclass. Keeps the
    last few turns only implicitly via whatever the caller passes in; this
    function performs no truncation itself.
    """
    lines = []
    for entry in transcript_context:
        role = entry.get("role", "user")
        text = entry.get("text", "")
        speaker = "Assistant" if role == "assistant" else "User"
        lines.append(f"{speaker}: {text}")
    lines.append(f"User: {user_text}")
    lines.append("Assistant:")
    return "\n".join(lines)


def generate_reply(
    transcript_context: list[dict],
    user_text: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Call Ollama's /api/generate with transcript context + new user text.

    Returns the generated reply text, stripped of leading/trailing whitespace.
    Raises LLMError on any failure (connection refused, timeout, malformed
    response, non-2xx status) - callers decide how to degrade (aggregator.py's
    _handle_ask falls back to FALLBACK_REPLY rather than crashing).
    """
    prompt = _build_prompt(transcript_context, user_text)
    try:
        response = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise LLMError(f"Ollama request failed: {exc}") from exc
    except ValueError as exc:  # json decode error
        raise LLMError(f"Ollama returned non-JSON response: {exc}") from exc

    reply = data.get("response")
    if not isinstance(reply, str) or not reply.strip():
        raise LLMError(f"Ollama response missing usable 'response' field: {data!r}")
    return reply.strip()
