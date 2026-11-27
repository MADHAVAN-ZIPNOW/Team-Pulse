"""Reads a meeting transcript into lines: Teams' own .vtt transcripts, or plain text with "Name: what was said"."""
from __future__ import annotations

import re
from html import unescape
from typing import Any

MAX_LINE = 2000
MERGE_UNTIL = 500  # consecutive pieces from the same speaker are joined into one line up to this many characters
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\s*-->")
_VOICE = re.compile(r"<v(?:\.[^ >]*)?\s+([^>]+)>", re.I)
_TAG = re.compile(r"</?[^>]+>")
_SPEAKER = re.compile(r"^\s*([^\W\d_][^:\n]{0,38}?)\s*:\s+(.+)$")


def _seconds(h: str | None, m: str, s: str, ms: str | None) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + (int((ms or "0").ljust(3, "0")) / 1000)


def _merge(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in lines:
        if out and out[-1]["speaker"] == ln["speaker"] and len(out[-1]["text"]) + len(ln["text"]) < MERGE_UNTIL:
            out[-1]["text"] += " " + ln["text"]
        else:
            out.append(dict(ln))
    return out


def parse_vtt(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("﻿", ""))
    for block in blocks:
        rows = [r for r in block.splitlines() if r.strip()]
        idx = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if idx is None or rows[0].startswith(("WEBVTT", "NOTE")):
            continue
        t = _TIME.search(rows[idx])
        payload = " ".join(rows[idx + 1 :])
        voice = _VOICE.search(payload)
        speaker = unescape(voice.group(1)).strip() if voice else "Speaker"
        body = unescape(_TAG.sub("", payload)).strip()
        if body:
            lines.append({"speaker": speaker[:60], "text": body[:MAX_LINE], "offset": _seconds(*t.groups()) if t else None})
    return _merge(lines)


def parse_plain(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for raw in text.replace("﻿", "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = _SPEAKER.match(raw)
        if m and "://" not in m.group(1):
            lines.append({"speaker": m.group(1).strip()[:60], "text": m.group(2).strip()[:MAX_LINE], "offset": None})
        elif lines and not m:
            lines[-1]["text"] = (lines[-1]["text"] + " " + raw)[:MAX_LINE]  # a wrapped continuation line
        else:
            lines.append({"speaker": "Speaker", "text": raw[:MAX_LINE], "offset": None})
    return _merge(lines)


def parse_transcript(text: str) -> list[dict[str, Any]]:
    """WebVTT (what Teams exports) or plain text. Returns [] when there is nothing to read."""
    return parse_vtt(text) if "-->" in text[:4000] else parse_plain(text)
