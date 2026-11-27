import asyncio
import json
from datetime import datetime, timezone

from app.services import radar_mail
from app.services.radar_service import RadarService, clean, parse


def _p(title, seen=("https://example.com/a",), fix=("https://example.com/fix",)):
    return {
        "title": title,
        "problem_sources": [{"url": u, "title": "t"} for u in seen],
        "solution_sources": [{"url": u, "title": "f"} for u in fix],
    }


KEY = "2026-11-26-morning"


def test_problem_without_a_real_source_link_is_dropped():
    raw = {"problems": [_p("Has source"), _p("No source", ()), _p("Bad link", ("not a url",))]}
    out = clean(raw, KEY, set())
    assert [p["title"] for p in out["problems"]] == ["Has source"]
    assert out["problems"][0]["rank"] == 1 and out["slot"] == "morning" and out["report_date"] == "2026-11-26"


def test_problem_and_solution_sources_stay_separate_and_old_sources_still_work():
    out = clean({"problems": [_p("A"), {"title": "Old", "sources": [{"url": "https://x.io/1"}]}]}, KEY, set())
    a, old = out["problems"]
    assert a["problem_sources"][0]["url"].endswith("/a") and a["solution_sources"][0]["url"].endswith("/fix")
    assert old["problem_sources"][0]["url"] == "https://x.io/1" and old["solution_sources"] == []


def test_repeats_are_dropped_and_size_is_capped():
    raw = {"problems": [_p("Seen Before!")] + [_p(f"P{i}") for i in range(12)]}
    out = clean(raw, KEY, {"seen before"})
    assert all(p["title"] != "Seen Before!" for p in out["problems"])
    assert len(out["problems"]) == 8 and [p["rank"] for p in out["problems"]] == list(range(1, 9))


def test_parse_finds_json_in_text_and_rejects_junk():
    assert parse('Here you go:\n{"problems": []}\nDone')["problems"] == []
    try:
        parse("nothing here")
    except Exception as exc:  # noqa: BLE001
        assert "JSON" in str(exc)
    else:
        raise AssertionError("expected an error")


class FakeClaude:
    def __init__(self, answer):
        self.answer, self.prompt, self.calls = answer, "", 0

    async def run_custom(self, prompt, options, timeout=0):
        self.prompt, self.calls = prompt, self.calls + 1
        return self.answer


class FakeMail:
    recipients = ["a@x.com", "b@x.com"]
    configured = True

    def __init__(self, fail=0):
        self.fail, self.sent = fail, []

    async def send(self, report):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("Mail.Send not approved")
        self.sent.append(report["key"])


CTX = {"domains": "Backend services, Databases", "watchlist": "FastAPI, React", "languages": "Python", "platforms": "Azure", "role": "Engineer", "skill": "Mid", "stack": "FastAPI"}
ANSWER = json.dumps({"daily_summary": "s", "problems": [_p("Issue one")]})


def _svc(tmp_path, claude, mail=None, hour=9):
    svc = RadarService(claude, "sonnet", str(tmp_path), (8, 14, 20), "UTC", CTX, mail)  # type: ignore[arg-type]
    svc._now = lambda: datetime(2026, 11, 26, hour, 0, tzinfo=timezone.utc)  # type: ignore[method-assign]
    return svc


def test_the_current_slot_follows_the_hour(tmp_path):
    cases = {5: "2026-11-25-night", 8: "2026-11-26-morning", 13: "2026-11-26-morning", 14: "2026-11-26-afternoon", 21: "2026-11-26-night"}
    for hour, key in cases.items():
        assert _svc(tmp_path, FakeClaude(ANSWER), hour=hour).current_key() == key
    assert "afternoon at 14:00" in _svc(tmp_path, FakeClaude(ANSWER), hour=9).next_run()


def test_run_saves_a_slot_report_emails_it_once_and_skips_known_titles(tmp_path):
    claude, mail = FakeClaude(ANSWER), FakeMail()
    svc = _svc(tmp_path, claude, mail)
    report = asyncio.run(svc.run())
    assert report["key"] == KEY and svc.get(KEY)["emailed_at"] and mail.sent == [KEY]
    assert "COMMUNITY FIRST" in claude.prompt and "morning" in claude.prompt
    assert svc._research_due() is False and svc._mail_due() is None  # nothing more to do in this slot
    svc._now = lambda: datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)  # type: ignore[method-assign]
    assert svc._research_due() is True  # the afternoon slot has no report yet
    asyncio.run(svc.run())
    assert "Issue one" in claude.prompt and svc.keys() == ["2026-11-26-afternoon", KEY]
    assert svc.get("../etc/passwd") is None


def test_a_failed_email_is_kept_and_retried_without_redoing_the_research(tmp_path):
    claude, mail = FakeClaude(ANSWER), FakeMail(fail=1)
    svc = _svc(tmp_path, claude, mail)
    asyncio.run(svc.run())
    saved = svc.get(KEY)
    assert saved["emailed_at"] is None and "Mail.Send" in saved["email_error"] and mail.sent == []
    svc._mail_failed_at = None  # the wait before retrying is over
    pending = svc._mail_due()
    assert pending is not None
    asyncio.run(svc.send_mail(pending))
    assert mail.sent == [KEY] and svc.get(KEY)["emailed_at"] and claude.calls == 1


def test_email_text_is_escaped_and_shows_where_problem_and_fix_were_seen():
    rep = clean({"daily_summary": "<script>x</script>", "problems": [_p("T<b>", ("https://a.io/p",), ("https://a.io/f",))]}, KEY, set())
    html = radar_mail.render(rep)
    assert "<script>" not in html and "&lt;script&gt;" in html and "T&lt;b&gt;" in html
    assert "SEEN AT" in html and "https://a.io/p" in html and "SOLUTION FOUND AT" in html and "https://a.io/f" in html
    assert radar_mail.subject(rep).startswith("Problem Radar")


def test_the_daily_prompt_covers_domains_and_a_focus_for_the_slot(tmp_path):
    claude = FakeClaude(ANSWER)
    svc = _svc(tmp_path, claude, hour=15)
    asyncio.run(svc.run())
    assert "Backend services, Databases" in claude.prompt and "backend services (APIs" in claude.prompt and '"domain"' in claude.prompt


def test_on_demand_search_runs_in_memory_and_is_not_saved(tmp_path):
    claude = FakeClaude(json.dumps({"daily_summary": "found", "problems": [{**_p("PG slow"), "domain": "Databases"}]}))
    svc = _svc(tmp_path, claude)

    async def go():
        sid = svc.start_search("  PostgreSQL   slow queries ", "Databases")
        assert svc.search_state(sid)["status"] == "running"
        await asyncio.gather(*svc.tasks)
        return sid

    sid = asyncio.run(go())
    state = svc.search_state(sid)
    assert state["status"] == "done" and state["query"] == "PostgreSQL slow queries"
    assert state["report"]["problems"][0]["domain"] == "Databases" and state["report"]["key"] == "search"
    assert "PostgreSQL slow queries" in claude.prompt and "Databases" in claude.prompt
    assert svc.keys() == []  # nothing was written to disk
    assert svc.search_state("nope") is None
    assert svc.status()["domains"] == ["Backend services", "Databases"]


def test_search_rejects_tiny_queries_and_reports_failures(tmp_path):
    svc = _svc(tmp_path, FakeClaude("not json"))
    try:
        svc.start_search("ab", "")
    except Exception as exc:  # noqa: BLE001
        assert "3 characters" in str(exc)
    else:
        raise AssertionError("expected an error")

    async def go():
        sid = svc.start_search("kubernetes crashloop", "")
        await asyncio.gather(*svc.tasks)
        return sid

    state = svc.search_state(asyncio.run(go()))
    assert state["status"] == "error" and "JSON" in state["error"]


def test_the_prompt_asks_for_community_questions_and_answers_and_not_for_one_stack(tmp_path):
    claude = FakeClaude(ANSWER)
    svc = _svc(tmp_path, claude)
    asyncio.run(svc.run())
    for word in ("Reddit", "Stack Overflow", "Hacker News", "Medium", "people_asked", "community_answer"):
        assert word in claude.prompt
    assert "Known stack" not in claude.prompt and "own stack" not in claude.prompt


def test_the_email_shows_what_people_asked_and_what_worked_and_has_no_colour_bar():
    rep = clean({"problems": [{**_p("T"), "people_asked": "Why does X fail?", "community_answer": "Upgrade to 2"}]}, KEY, set())
    html = radar_mail.render(rep)
    assert "People ask:" in html and "Why does X fail?" in html and "What worked (community):" in html and "Upgrade to 2" in html
    assert "border-left" not in html


def test_the_email_shows_everything_in_full_with_a_do_today_list():
    steps = [f"step {i}" for i in range(1, 9)]
    srcs = [{"url": f"https://a.io/{i}", "title": f"S{i}", "source_type": "reddit", "key_finding": "k" * 300} for i in range(6)]
    prob = {
        "title": "Big", "severity": "high", "problem_sources": srcs, "solution_sources": srcs[:1], "root_cause": "RC", "affected_versions_or_services": "AFF",
        "symptoms": ["sym1", "sym2"], "evidence_strength": "strong",
        "recommended_action": {"summary": "Do the fix", "steps": steps, "risks": ["risk1"], "verification_steps": ["verify1"]},
        "alternative_workarounds": [{"summary": "alt1", "when_to_use": "now"}],
    }
    rep = clean({"problems": [prob], "tomorrows_focus": ["Kubernetes"]}, KEY, set())
    html = radar_mail.render(rep)
    assert all(f"step {i}" in html for i in range(1, 9))  # no step is cut
    assert all(f"https://a.io/{i}" in html for i in range(6)) and "k" * 300 in html  # no source or finding is cut
    for word in ("DO TODAY", "AT A GLANCE", "Do the fix", "Who is affected", "Root cause", "SYMPTOMS", "CHECK IT WORKED", "WATCH OUT",
                 "OTHER WORKAROUNDS", "Kubernetes", "Reddit", "evidence"):
        assert word in html, word


def test_a_folder_path_mangled_by_git_bash_is_repaired(monkeypatch):
    from app.config import _dir

    monkeypatch.setenv("RADAR_DIR", "C:/Program Files/Git/home/app/data/radar")
    assert _dir("RADAR_DIR", "/x") == "/home/app/data/radar"
    monkeypatch.setenv("RADAR_DIR", "/home/app/data/radar")
    assert _dir("RADAR_DIR", "/x") == "/home/app/data/radar"
    monkeypatch.setenv("RADAR_DIR", "C:/Users/me/radar")  # a real Windows path is left alone
    assert _dir("RADAR_DIR", "/x") == "C:/Users/me/radar"
    monkeypatch.delenv("RADAR_DIR")
    assert _dir("RADAR_DIR", "/x") == "/x"
