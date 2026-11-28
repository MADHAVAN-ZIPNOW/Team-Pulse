"""Fast speech-to-text with Groq's free Whisper API (whisper-large-v3-turbo): about a minute for a 30-minute meeting.

Only the AUDIO is sent to Groq (never the video). The key comes from GROQ_API_KEY (a Groq key starts with "gsk_").
Long audio is cut into chunks under Groq's 25 MB limit; every line keeps its time in the meeting. The result has the same shape as
speech_local.transcribe: a list of {"speaker", "text", "offset"} lines.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

import httpx

from .speech_azure import MediaError
from .speech_local import collapse_repeats

logger = logging.getLogger("pr_assistant.groq")

URL = "https://api.groq.com/openai/v1/audio/transcriptions"  # OpenAI-compatible endpoint
MAX_BYTES = 24 * 1024 * 1024  # Groq accepts files up to about 25 MB
MAX_CHUNK_SECONDS = 20 * 60
TRIES = 5


def model() -> str:
    return os.getenv("GROQ_MODEL", "whisper-large-v3-turbo").strip() or "whisper-large-v3-turbo"


def api_key() -> str:
    """The Groq key, or "" when there is none. (GROK_API_KEY is accepted too, a common spelling slip, but only if it is a Groq key.)"""
    for name in ("GROQ_API_KEY", "GROK_API_KEY"):
        v = os.getenv(name, "").strip().strip("\"'")
        if v.startswith("gsk_"):
            return v
    return ""


def available() -> bool:
    return bool(api_key())


async def _split(audio: str, workdir: Path, ffmpeg: str) -> list[tuple[Path, float]]:
    """(file, start second) for each part, each part under 24 MB. A small file is sent as it is."""
    src = Path(audio)
    size = src.stat().st_size
    if size <= MAX_BYTES:
        return [(src, 0.0)]
    # this app's audio is 64 kbps (8000 bytes a second); cut so that every part stays well under the limit
    seconds = max(60, min(MAX_CHUNK_SECONDS, int(MAX_BYTES * 0.8 / 8000)))
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-f", "segment", "-segment_time", str(seconds),
        "-c", "copy", "-reset_timestamps", "1", str(workdir / "part_%04d.mp3"),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    parts = sorted(workdir.glob("part_*.mp3"))
    if proc.returncode != 0 or not parts:
        raise MediaError("Could not split the audio: " + err.decode("utf-8", "replace")[-200:])
    return [(p, i * float(seconds)) for i, p in enumerate(parts)]


async def _send(http: httpx.AsyncClient, path: Path, key: str) -> dict[str, Any]:
    """One audio file to Groq, retrying on rate limits (429), server errors and network problems."""
    data = {"model": model(), "response_format": "verbose_json", "temperature": "0"}
    # WHISPER_LANGUAGE: not set = English (the old default); set to a code such as "ta" = that language; set but EMPTY = let Whisper
    # detect the language itself (best for meetings that mix Tamil and English).
    language = os.getenv("WHISPER_LANGUAGE")
    language = "en" if language is None else language.strip()
    if language:
        data["language"] = language
    last = ""
    for attempt in range(1, TRIES + 1):
        wait = min(2 ** attempt, 30)
        try:
            res = await http.post(URL, headers={"Authorization": f"Bearer {key}"}, data=data,
                                  files={"file": (path.name, path.read_bytes(), "audio/mpeg")}, timeout=300)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = f"network problem: {exc}"
        else:
            if res.status_code == 200:
                return res.json()
            last = f"HTTP {res.status_code}: {res.text[:200]}"
            if res.status_code in (401, 403):
                raise MediaError("Groq refused the API key. Check GROQ_API_KEY.")
            if res.status_code == 429:
                try:
                    wait = max(wait, min(float(res.headers.get("retry-after", 0)), 120))
                except ValueError:
                    pass
            elif res.status_code < 500:
                raise MediaError(f"Groq rejected the audio: {last}")
        if attempt < TRIES:
            logger.warning("Groq try %d/%d failed (%s); retrying in %.0fs", attempt, TRIES, last, wait)
            await asyncio.sleep(wait)
    raise MediaError(f"Groq did not answer after {TRIES} tries: {last}")


async def transcribe(audio: str, ffmpeg: str, on_progress: Callable[[float, float], None] | None = None) -> list[dict[str, Any]]:
    key = api_key()
    if not key:
        raise MediaError("GROQ_API_KEY is not set.")
    workdir = Path(tempfile.mkdtemp(prefix="groq-"))
    try:
        parts = await _split(audio, workdir, ffmpeg)
        segs: list[tuple[float, float, str]] = []
        async with httpx.AsyncClient() as http:
            for i, (path, start) in enumerate(parts, 1):
                result = await _send(http, path, key)
                for s in result.get("segments") or []:
                    text = collapse_repeats(str(s.get("text") or "").strip())
                    if text:
                        segs.append((start + float(s.get("start", 0)), start + float(s.get("end", 0)), text))
                if not result.get("segments") and str(result.get("text") or "").strip():
                    segs.append((start, start, collapse_repeats(str(result["text"]).strip())))
                if on_progress:
                    on_progress(float(i), float(len(parts)))
        return _lines(segs)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _lines(segs: list[tuple[float, float, str]]) -> list[dict[str, Any]]:
    """Joins short neighbouring segments into lines (about 400 characters), each keeping the time its first word was said."""
    lines: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    last_end = 0.0
    for start, end, text in segs:
        if cur and len(cur["text"]) + len(text) < 400 and start - last_end < 1.5:
            cur["text"] += " " + text
        else:
            cur = {"speaker": "Speaker", "text": text, "offset": round(start, 1)}
            lines.append(cur)
        last_end = end
    return lines
