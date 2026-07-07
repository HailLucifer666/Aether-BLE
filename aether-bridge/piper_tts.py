"""Aether Protocol Phase 8 - shared local Piper TTS synthesis path.

Single source of truth for turning text into audio via Piper (local, ONNX-
based, no cloud), used by BOTH aggregator.py's _generate_speech (wraps the
output as a WAV data: URL for the dashboard, same contract edge-tts used to
fulfil) and wyoming_satellite.py's Synthesize handler (streams raw PCM to
Home Assistant) - per docs/phase8/ARCHITECTURE.md's requirement that both
paths go through "the SAME Piper TTS path".

Model file location: a Piper voice model + its .json config, resolved via
(in priority order) the AETHER_PIPER_MODEL env var, then the default bundled
path models/piper/en_US-lessac-medium.onnx. Any failure to load the model or
synthesize (missing file, corrupt model, onnxruntime error) is caught by
callers - this module raises PiperTTSError rather than crashing, so a caller
can fall back to a synthetic utterance exactly like the old edge-tts path.
"""

import os
from pathlib import Path

PIPER_SAMPLE_RATE = 22050  # overwritten with the loaded voice's real rate once available
DEFAULT_MODEL_PATH = Path(__file__).parent / "models" / "piper" / "en_US-lessac-medium.onnx"

_voice_cache = None  # lazily loaded PiperVoice singleton; avoids reloading the ONNX model per call


class PiperTTSError(Exception):
    """Raised on any Piper load/synthesis failure; callers fall back gracefully."""


def _resolve_model_path() -> Path:
    override = os.environ.get("AETHER_PIPER_MODEL")
    if override:
        return Path(override)
    return DEFAULT_MODEL_PATH


def _load_voice():
    global _voice_cache
    if _voice_cache is not None:
        return _voice_cache

    try:
        from piper import PiperVoice
    except Exception as exc:  # noqa: BLE001 - any import failure -> caller falls back
        raise PiperTTSError(f"piper-tts package unavailable: {exc}") from exc

    model_path = _resolve_model_path()
    if not model_path.exists():
        raise PiperTTSError(f"Piper voice model not found at {model_path}")

    try:
        voice = PiperVoice.load(str(model_path))
    except Exception as exc:  # noqa: BLE001 - corrupt model / onnxruntime error -> fall back
        raise PiperTTSError(f"Failed to load Piper voice model: {exc}") from exc

    _voice_cache = voice
    return voice


def synthesize_pcm(text: str) -> tuple[bytes, int]:
    """Synthesize `text` and return (raw_pcm_int16_bytes, sample_rate).

    Mono, 16-bit signed PCM, little-endian - the format both the WAV wrapper
    below and wyoming_satellite.py's AudioChunk streaming expect. Raises
    PiperTTSError on any failure (model missing, synthesis error, empty
    output) - callers decide the fallback behavior.
    """
    voice = _load_voice()
    try:
        chunks = list(voice.synthesize(text))
    except Exception as exc:  # noqa: BLE001 - any synthesis-time failure -> fall back
        raise PiperTTSError(f"Piper synthesis failed: {exc}") from exc

    if not chunks:
        raise PiperTTSError("Piper synthesis returned no audio chunks")

    audio_bytes = b"".join(chunk.audio_int16_bytes for chunk in chunks)
    if not audio_bytes:
        raise PiperTTSError("Piper synthesis returned empty audio")

    sample_rate = chunks[0].sample_rate
    return audio_bytes, sample_rate


def synthesize_speech(text: str) -> tuple[bytes | None, int | None]:
    """Non-raising wrapper for synthesize_pcm, used by wyoming_satellite.py.

    Returns (None, None) on any failure instead of raising, so a Wyoming
    Synthesize handler can degrade to an empty audio stream rather than
    dropping the connection.
    """
    try:
        return synthesize_pcm(text)
    except PiperTTSError:
        return None, None


def pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM bytes in a minimal WAV container (in-memory, no file I/O)."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def estimate_duration_ms(pcm_byte_count: int, sample_rate: int, channels: int = 1, sample_width: int = 2) -> int:
    """Duration in milliseconds implied by a raw PCM byte count."""
    bytes_per_sample_frame = channels * sample_width
    if bytes_per_sample_frame <= 0 or sample_rate <= 0:
        return 0
    num_frames = pcm_byte_count // bytes_per_sample_frame
    return int((num_frames / sample_rate) * 1000)
