import asyncio

import pytest

from app.services import speech_groq
from app.services.speech_azure import MediaError


def test_only_a_groq_key_is_accepted(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROK_API_KEY", "xai-not-a-groq-key")
    assert speech_groq.api_key() == "" and not speech_groq.available()
    monkeypatch.setenv("GROK_API_KEY", '"gsk_abc"')  # the common spelling slip still works for a real Groq key
    assert speech_groq.api_key() == "gsk_abc"
    monkeypatch.setenv("GROQ_API_KEY", "gsk_right")
    assert speech_groq.api_key() == "gsk_right"


def test_segments_become_lines_that_keep_their_time():
    segs = [(0.0, 3.0, "Hello everyone."), (3.5, 6.0, "Thanks for joining."), (20.0, 24.0, "Next topic.")]
    lines = speech_groq._lines(segs)
    assert [ln["offset"] for ln in lines] == [0.0, 20.0]
    assert lines[0]["text"] == "Hello everyone. Thanks for joining." and lines[0]["speaker"] == "Speaker"


def test_chunks_continue_the_clock_and_a_bad_key_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    calls = []

    async def fake_send(http, path, key):
        calls.append(path.name)
        return {"segments": [{"start": 1.0, "end": 2.0, "text": "hi there"}]}

    monkeypatch.setattr(speech_groq, "_send", fake_send)
    lines = asyncio.run(speech_groq.transcribe(str(audio), "ffmpeg"))
    assert calls == ["a.mp3"] and lines[0]["offset"] == 1.0 and lines[0]["text"] == "hi there"

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    with pytest.raises(MediaError):
        asyncio.run(speech_groq.transcribe(str(audio), "ffmpeg"))
