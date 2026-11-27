import asyncio
from types import SimpleNamespace

import pytest

from app.services import meeting_auto, meeting_import
from app.services.graph_teams import GraphError
from app.services.meeting_agent import MeetingAgent
from app.services.meeting_auto import GIVE_UP_AFTER, MAX_PER_CHECK, AutoProcessor
from app.services.meeting_store import MeetingStore

FUTURE, PAST = "9998-01-01T00:00:00Z", "2000-01-01T00:00:00Z"


class FakeGraph:
    def __init__(self, events, ready=True, fail=False):
        self.events, self.ready, self.fail = events, ready, fail

    async def calendar(self, back, ahead):
        if self.fail:
            raise GraphError("Teams said no", 403)
        return self.events

    async def online_meeting_id(self, join_url):
        return "om-" + join_url

    async def has_content(self, online_id):
        return self.ready


def ev(n, **over):
    base = {"id": f"e{n}", "subject": f"Meeting {n}", "start": PAST, "end": FUTURE, "joinUrl": f"https://teams/{n}", "state": "finished", "isOrganizer": True, "organizer": "Me"}
    base.update(over)
    return base


class Rig:
    def __init__(self, tmp_path, monkeypatch, graph):
        self.store = MeetingStore(tmp_path, "secret")
        self.agent = MeetingAgent(SimpleNamespace(), settle=0, store=self.store)
        self.settings = SimpleNamespace(azure_devops_api_version="7.1")
        self.session = SimpleNamespace(user=SimpleNamespace(object_id="u1", email=None, name="N"), msal_app=object())
        self.graph = graph
        self.imported = []
        self.auto = self.make()
        monkeypatch.setattr(meeting_import, "import_teams", self.fake_import)

    def make(self):
        auto = AutoProcessor(self.agent, SimpleNamespace(), SimpleNamespace(active=lambda: [self.session]), None, self.settings)
        auto._graph = lambda session: self.graph
        return auto

    async def fake_import(self, agent, graph, owner, mid, join_url, client, settings):
        self.imported.append((mid, join_url))
        agent.set_stage(owner, mid, "")

    def check(self):
        asyncio.run(self.auto.check(self.session))

    def meetings(self):
        return self.agent.list("u1")


def rig(tmp_path, monkeypatch, events, **kw):
    return Rig(tmp_path, monkeypatch, FakeGraph(events, **kw))


def test_nothing_happens_until_the_person_switches_it_on(tmp_path, monkeypatch):
    r = rig(tmp_path, monkeypatch, [ev(1)])
    r.check()
    assert r.meetings() == [] and r.auto.status("u1")["enabled"] is False


def test_the_switch_and_its_project_are_saved_and_survive_a_restart(tmp_path, monkeypatch):
    r = rig(tmp_path, monkeypatch, [])
    st = r.auto.set("u1", True, "dtek", " Alpha ")
    assert st["enabled"] and st["project"] == "Alpha" and st["enabledAt"]
    again = r.make()  # "the server restarted": settings come back from the encrypted file
    assert again.status("u1")["enabled"] and again.status("u1")["project"] == "Alpha" and again.status("u1")["organization"] == "dtek"
    assert not list(tmp_path.rglob("*.json")) and b"Alpha" not in b"".join(f.read_bytes() for f in tmp_path.rglob("*.bin"))  # nothing readable on disk
    assert r.auto.set("u1", False, "", "Alpha")["enabled"] is False
    assert r.auto.set("u1", True, "bad org!", "")["organization"] == "dtek"  # a bad organization name is ignored


def test_only_finished_meetings_i_organized_that_ended_after_switching_on(tmp_path, monkeypatch):
    events = [
        ev(1),                                       # yes
        ev(2, isOrganizer=False),                    # someone else's
        ev(3, state="upcoming"), ev(4, state="live"),
        ev(5, end=PAST),                             # ended before I turned it on
    ]
    r = rig(tmp_path, monkeypatch, events)
    r.auto.set("u1", True, "dtek", "Alpha")
    r.check()
    assert [m["title"] for m in r.meetings()] == ["Meeting 1"]
    m = r.meetings()[0]
    assert m["auto"] is True and m["project"] == "Alpha" and m["source"] == "teams" and m["organization"] == "dtek"
    assert [j for _, j in r.imported] == ["https://teams/1"]


def test_each_meeting_is_processed_once_even_if_deleted_afterwards(tmp_path, monkeypatch):
    r = rig(tmp_path, monkeypatch, [ev(1)])
    r.auto.set("u1", True, "dtek", "Alpha")
    r.check()
    r.check()
    assert len(r.imported) == 1 and len(r.meetings()) == 1
    r.agent.delete("u1", r.meetings()[0]["id"])  # the person deletes it: it must not come back
    r.check()
    assert r.meetings() == [] and len(r.imported) == 1
    assert r.make().status("u1")["processed"] == 1  # remembered across restarts too


def test_waits_for_teams_to_finish_the_transcript_then_gives_up(tmp_path, monkeypatch):
    r = rig(tmp_path, monkeypatch, [ev(1)], ready=False)
    r.auto.set("u1", True, "dtek", "Alpha")
    for _ in range(GIVE_UP_AFTER - 1):
        r.check()
    assert r.meetings() == [] and r.auto.status("u1")["processed"] == 0  # still waiting: no empty meeting is created
    r.graph.ready = True
    r.check()
    assert len(r.meetings()) == 1  # the transcript arrived in time
    r2 = rig(tmp_path / "b", monkeypatch, [ev(1)], ready=False)
    r2.auto.set("u1", True, "dtek", "")
    for _ in range(GIVE_UP_AFTER):
        r2.check()
    r2.graph.ready = True
    r2.check()
    assert r2.meetings() == [] and r2.auto.status("u1")["processed"] == 1  # it stopped trying: nothing ever appeared


def test_at_most_three_meetings_per_check(tmp_path, monkeypatch):
    r = rig(tmp_path, monkeypatch, [ev(n) for n in range(1, 8)])
    r.auto.set("u1", True, "dtek", "Alpha")
    r.check()
    assert len(r.meetings()) == MAX_PER_CHECK
    r.check()
    assert len(r.meetings()) == 2 * MAX_PER_CHECK  # the rest follow on the next checks


def test_a_teams_problem_is_shown_and_does_not_crash_or_create_anything(tmp_path, monkeypatch):
    r = rig(tmp_path, monkeypatch, [ev(1)], fail=True)
    r.auto.set("u1", True, "dtek", "Alpha")
    r.check()
    assert r.meetings() == [] and r.auto.status("u1")["lastError"] == "Teams said no"
    r.graph.fail = False
    r.check()
    assert len(r.meetings()) == 1 and r.auto.status("u1")["lastError"] == "" and r.auto.status("u1")["lastCheck"]


def test_the_loop_skips_sessions_that_cannot_read_teams(tmp_path, monkeypatch):
    r = rig(tmp_path, monkeypatch, [ev(1)])
    r.auto.set("u1", True, "dtek", "Alpha")
    r.session.msal_app = None  # signed in with the Azure CLI code: no Teams access

    async def one_round():
        task = asyncio.create_task(r.auto.run_forever())
        monkeypatch.setattr(meeting_auto, "INTERVAL_SECONDS", 3600)
        await asyncio.sleep(0.05)
        task.cancel()

    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _noop())
    with pytest.raises((asyncio.CancelledError, RuntimeError, Exception)):
        asyncio.run(asyncio.wait_for(r.auto.run_forever(), timeout=0.2))
    assert r.meetings() == []


async def _noop():
    raise asyncio.CancelledError  # stops the loop after the first pass
