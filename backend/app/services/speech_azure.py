"""Turns a meeting recording into a transcript: ffmpeg takes the audio out, Azure AI Speech ("fast transcription") writes the text.

Tamil and English are both tried per phrase, and different speakers are told apart. This is a better transcript than Teams' own.
Needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION (a Speech resource in your Azure tenant, so the audio stays in your tenant) and ffmpeg.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("pr_assistant.speech")

MAX_AUDIO_BYTES = 280 * 1024 * 1024  # Azure fast transcription takes up to 300 MB / 2 hours
FFMPEG_TIMEOUT = 1200
MAX_LINE = 2000


class MediaError(Exception):
    """Message is safe to show to the user."""


def find_ffmpeg(configured: str = "") -> str | None:
    return configured if configured and Path(configured).is_file() else shutil.which("ffmpeg")


async def extract_audio(src: str, dst: str, ffmpeg: str) -> None:
    """Video or audio in, small mono 16 kHz MP3 out (about 30 MB per hour)."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", dst,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise MediaError("Reading the recording took too long.") from exc
    if proc.returncode != 0:
        logger.warning("ffmpeg failed: %s", err.decode("utf-8", "replace")[-300:])
        raise MediaError("That file could not be read as audio or video.")
    if Path(dst).stat().st_size > MAX_AUDIO_BYTES:
        raise MediaError("That recording is longer than two hours. Split it and import the parts.")


def phrases_to_lines(phrases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Azure's phrases (each with an optional speaker number) -> lines, joining consecutive phrases of one speaker."""
    lines: list[dict[str, Any]] = []
    for p in sorted(phrases, key=lambda p: p.get("offsetMilliseconds", 0)):
        text = str(p.get("text") or "").strip()
        if not text:
            continue
        who = f"Speaker {p['speaker']}" if p.get("speaker") not in (None, 0) else "Speaker"
        if lines and lines[-1]["speaker"] == who and len(lines[-1]["text"]) + len(text) < 500:
            lines[-1]["text"] += " " + text
        else:
            lines.append({"speaker": who, "text": text[:MAX_LINE], "offset": round(p.get("offsetMilliseconds", 0) / 1000, 1)})
    return lines


async def transcribe(path: str, key: str, region: str, locales: list[str], http: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    url = f"https://{region}.api.cognitive.microsoft.com/speechtotext/transcriptions:transcribe"
    definition = {"locales": locales, "diarization": {"enabled": True, "maxSpeakers": 10}, "profanityFilterMode": "None"}
    own = http is None
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(1800.0, connect=15.0))
    try:
        with open(path, "rb") as f:
            res = await client.post(
                url, params={"api-version": "2024-11-15"}, headers={"Ocp-Apim-Subscription-Key": key},
                files={"audio": (Path(path).name, f, "audio/mpeg")}, data={"definition": json.dumps(definition)},
            )
    except httpx.HTTPError as exc:
        raise MediaError("Could not reach Azure Speech.") from exc
    finally:
        if own:
            await client.aclose()
    if res.status_code in (401, 403):
        raise MediaError("Azure Speech rejected the key. Check AZURE_SPEECH_KEY and AZURE_SPEECH_REGION.")
    if res.status_code == 429:
        raise MediaError("Azure Speech is busy. Try again in a minute.")
    if res.status_code >= 400:
        logger.warning("Azure Speech HTTP %s: %s", res.status_code, res.text[:200])
        raise MediaError(f"Azure Speech could not transcribe it (HTTP {res.status_code}).")
    try:
        lines = phrases_to_lines(res.json().get("phrases") or [])
    except ValueError as exc:
        raise MediaError("Azure Speech returned something unreadable.") from exc
    if not lines:
        raise MediaError("No speech was found in that recording.")
    return lines
