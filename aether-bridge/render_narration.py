"""Render demo narration lines to standalone MP3 files via Microsoft edge-tts.

Reuses the same edge_tts.Communicate(...).stream() pattern proven out in
aggregator.py's _generate_speech, but writes raw MP3 bytes to disk instead of
streaming a base64 data URL over a WebSocket - for lining up voiceover audio
in a video editor.
"""

import argparse
import asyncio
import sys
from pathlib import Path

DEFAULT_VOICE = "en-US-AriaNeural"
DEFAULT_OUTPUT_DIR = "narration_out"
SYNTHETIC_MS_PER_CHAR = 60

DEFAULT_NARRATION_LINES = [
    "Say 'Hey Google' near three devices - they all answer. This has been broken for a decade.",
    "The fundamental issue is RF noise. Watch - naive mode flaps ownership five times in ten seconds.",
    "Now flip hysteresis on. Same room, same walk - one handoff. A challenger has to beat the current owner by 5 decibels for two consecutive readings. That's the primitive the whole protocol builds on.",
    "This isn't a simulation. Switching to Live BLE - real Bluetooth, real phone, real signal.",
    "Now the real system. Multiple devices, continuous election - not at the wake-word instant, every 400 milliseconds. Exactly one owns the conversation.",
    "Scanner-A currently owns me. It has the strongest signal. Watch the handoff log - when I 'walk' closer to Scanner-B, ownership migrates.",
    "Now the thing nobody else can do. I type a sentence - the owner speaks it.",
]


def _load_lines(text_file: str | None) -> list[str]:
    if text_file is None:
        return DEFAULT_NARRATION_LINES
    path = Path(text_file)
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        raise ValueError(f"{text_file} contained no narration lines.")
    return lines


async def _render_line(index: int, text: str, voice: str, output_dir: Path) -> None:
    out_path = output_dir / f"beat_{index:02d}.mp3"
    try:
        import edge_tts
    except Exception as exc:  # noqa: BLE001 - any import failure -> report and skip
        print(f"[narration] beat {index:02d}: edge-tts unavailable ({exc}); skipped.")
        return

    try:
        communicate = edge_tts.Communicate(text, voice=voice)
        chunks: list[bytes] = []
        total_ms = 0
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                data = chunk.get("data")
                if isinstance(data, bytes):
                    chunks.append(data)
            elif chunk.get("type") == "WordBoundary":
                offset = chunk.get("offset", 0)
                duration = chunk.get("duration", 0)
                total_ms = max(total_ms, int((offset + duration) / 10_000))
        audio_bytes = b"".join(chunks)
        if not audio_bytes:
            raise RuntimeError("edge-tts returned no audio data")
        if total_ms <= 0:
            # ~1 KB mp3 per second of 24kbps neural speech as a rough
            # estimate when WordBoundary metadata is unavailable.
            total_ms = max(len(text) * SYNTHETIC_MS_PER_CHAR, int(len(audio_bytes) / 1.0))
        out_path.write_bytes(audio_bytes)
        print(f"[narration] beat {index:02d}: {out_path} ({total_ms}ms) - {text[:60]!r}")
    except Exception as exc:  # noqa: BLE001 - network/service failure -> report and skip
        print(f"[narration] beat {index:02d}: edge-tts generation failed ({exc}); skipped.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Aether demo narration lines to MP3 via edge-tts.")
    parser.add_argument("--text-file", default=None, help="Path to a file with one narration line per line (default: built-in 7-beat demo script).")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"edge-tts voice name (default: {DEFAULT_VOICE!r}).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Output directory for rendered MP3s (default: {DEFAULT_OUTPUT_DIR!r}).")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> None:
    lines = _load_lines(args.text_file)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, text in enumerate(lines, start=1):
        await _render_line(index, text, args.voice, output_dir)


def main() -> None:
    args = parse_args(sys.argv[1:])
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
