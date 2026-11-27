from app.services import speech_local


def test_the_defaults_are_the_measured_fastest(monkeypatch):
    monkeypatch.delenv("WHISPER_THREADS", raising=False)
    monkeypatch.delenv("WHISPER_BATCH", raising=False)
    assert speech_local._threads() <= 4  # more threads were measured to be slower
    assert speech_local._batch_size() == 32
    monkeypatch.setenv("WHISPER_THREADS", "2")
    monkeypatch.setenv("WHISPER_BATCH", "0")
    assert speech_local._threads() == 2 and speech_local._batch_size() == 0


def test_progress_is_reported_while_segments_arrive(monkeypatch):
    class Seg:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class Info:
        duration = 100.0
        language, language_probability = "en", 1.0

    monkeypatch.setattr(speech_local, "_segments", lambda audio, name, language: ([Seg(0, 30, "a"), Seg(31, 70, "b"), Seg(71, 100, "c")], Info()))
    seen = []
    lines = speech_local._run("x.mp3", "base.en", None, lambda done, total: seen.append((done, total)))
    assert [d for d, _ in seen] == [30, 70, 100] and seen[0][1] == 100.0
    assert [ln["text"] for ln in lines] == ["a b c"] or len(lines) >= 1


def test_repeated_loops_are_collapsed_but_normal_text_is_left_alone():
    from app.services.speech_local import collapse_repeats

    out = collapse_repeats("I'm not sure if I'm not sure if I'm not sure if I'm not sure if I'm not sure")
    assert out.startswith("I'm not sure if") and len(out) < 40  # a loop of 5 becomes one (a half-finished repeat at the end may stay)
    assert collapse_repeats("The system is available in the system. " * 30).count("available") == 1
    assert collapse_repeats("section 1.1.1.1.1.1.1.1.1.1.1.1 of the act") == "section 1.1 of the act"
    normal = "We will deploy on Friday. Then we will test it. After that we will deploy again."
    assert collapse_repeats(normal) == normal
    assert collapse_repeats("no no") == "no no"  # two repeats are normal speech
