"""Free, local speech-to-text (Whisper via faster-whisper): the recording's audio -> transcript lines. No cloud service, no cost.

The model is multilingual, so Tamil and English are both understood. A bigger model (WHISPER_MODEL=medium) is more accurate for Tamil
but slower and needs more memory. Whisper runs on this server's CPU, so a long meeting takes a few minutes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from typing import Any

logger = logging.getLogger("pr_assistant.whisper")

_models: dict[str, Any] = {}


def available() -> bool:
    """Is the library installed? Only looks, never imports it: importing it can take minutes and must never block start-up."""
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def _cache_dir() -> str | None:
    """Where the speech model is kept: the first folder we are allowed to write to. (An older image points HF_HOME at /opt/hf, which
    the app user cannot write to, so the model could never be downloaded.) The data volume survives restarts, so it comes first after HF_HOME."""
    for base in (os.getenv("HF_HOME"), "/home/app/data/hf", os.path.join(tempfile.gettempdir(), "hf")):
        if not base:
            continue
        folder = os.path.join(base, "hub")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            continue
        if os.access(folder, os.W_OK):
            return folder
    return None


def _threads() -> int:
    """How many processor threads Whisper may use (WHISPER_THREADS). Measured on this PC: 4 threads are the fastest. More threads are SLOWER
    (8 threads ran 2.5x slower, 12 threads 7x slower) because the threads fight over the same cores."""
    try:
        wanted = int(os.getenv("WHISPER_THREADS", "") or 0)
    except ValueError:
        wanted = 0
    return max(1, wanted or min(4, os.cpu_count() or 4))


def _model(name: str) -> Any:
    if name not in _models:
        from faster_whisper import WhisperModel

        _models[name] = WhisperModel(name, device="cpu", compute_type="int8", download_root=_cache_dir(), cpu_threads=_threads())
    return _models[name]


GUARD = {
    "repetition_penalty": 1.15,  # a word that was just said is less likely to be said again
    "no_repeat_ngram_size": 5,  # never repeat the same 5 words in a row
    "compression_ratio_threshold": 2.0,  # text that compresses very well is a loop: throw it away and retry
    "log_prob_threshold": -1.0,
    "no_speech_threshold": 0.6,
}
_LOOP = re.compile(r"(\b[\w'’ ,;:-]{3,160}?[.!?]?)(?:\s+\1){2,}", re.I)
_DIGITS = re.compile(r"(\d+[.,])\1{3,}")


def collapse_repeats(text: str) -> str:
    """Safety net: a sentence or phrase repeated 3 or more times in a row is kept once ("I'm not sure if I'm not sure if ..." -> once)."""
    text = _DIGITS.sub(r"\1", text)
    for _ in range(6):
        shorter = _LOOP.sub(r"\1", text)
        if shorter == text:
            break
        text = shorter
    return text.strip()


def _batch_size() -> int:
    """WHISPER_BATCH: how many pieces of audio are processed together. Measured on this PC: batch 32 with 4 threads is about 21x real time,
    against about 1x for the normal mode with 8 threads. 0 or 1 = off."""
    try:
        return max(0, int(os.getenv("WHISPER_BATCH", "32") or 0))
    except ValueError:
        return 32


def _segments(audio: str, name: str, language: str | None) -> tuple[Any, Any]:
    model = _model(name)
    batch = _batch_size()
    if batch > 1:
        try:
            from faster_whisper import BatchedInferencePipeline

            return BatchedInferencePipeline(model).transcribe(audio, language=language, batch_size=batch, beam_size=1, **GUARD)
        except Exception as exc:  # noqa: BLE001 - an older library or an odd file: use the normal mode, which always works
            logger.warning("Batched Whisper is not available (%s); using the normal mode", exc)
    return model.transcribe(audio, language=language, vad_filter=True, beam_size=1, condition_on_previous_text=False, temperature=[0.0, 0.2, 0.4], **GUARD)


def _run(audio: str, name: str, language: str | None, on_progress: Any = None) -> list[dict[str, Any]]:
    # The team speaks English only: tell Whisper so it skips guessing the language (WHISPER_LANGUAGE="" turns this off).
    language = language or (os.getenv("WHISPER_LANGUAGE", "en").strip() or None)
    segments, info = _segments(audio, name, language)
    logger.info("Whisper heard %s (%.0f%% sure)", info.language, info.language_probability * 100)
    lines: list[dict[str, Any]] = []
    total = float(getattr(info, "duration", 0) or 0)
    for s in segments:
        if on_progress is not None and total:
            on_progress(min(float(s.end), total), total)  # how much of the audio has been written so far
        text = collapse_repeats(s.text.strip())
        if not text:
            continue
        if lines and s.start - lines[-1]["_end"] < 1.5 and len(lines[-1]["text"]) + len(text) < 400:
            lines[-1]["text"] += " " + text
            lines[-1]["_end"] = s.end
        else:
            lines.append({"speaker": "Speaker", "text": text, "offset": round(s.start, 1), "_end": s.end})
    for ln in lines:
        ln.pop("_end", None)
    return lines


async def warm(name: str) -> None:
    """Downloads/loads the model in the background at start-up, so the first meeting does not wait for it."""
    try:
        await asyncio.sleep(300)  # let the app and Claude start first: loading this library keeps the processor busy for minutes
        await asyncio.to_thread(_model, name)
        logger.info("Whisper model '%s' is ready", name)
    except Exception as exc:  # noqa: BLE001 - no internet yet, disk full ...: it is tried again when a meeting needs it
        logger.warning("Whisper model '%s' could not be loaded yet: %s", name, exc)


async def transcribe(audio: str, model: str, language: str | None = None, on_progress: Any = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_run, audio, model, language, on_progress)
