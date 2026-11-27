import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.azure_devops.client import AzureDevOpsClient, AzureDevOpsError
from app.services.meeting_agent import WAKE, MeetingAgent, map_type, parse_plan


def plan(**over):
    base = {"intent": "create_work_items", "confidence": 0.9, "project": "", "items": [
        {"type": "pbi", "title": "Login page", "description": "Users can sign in.", "acceptance_criteria": "Works on mobile",
         "tasks": [{"title": "Design", "hours": 4}, {"title": "Build", "hours": None}]}]}
    base.update(over)
    return json.dumps(base)


class StubClaude:
    def __init__(self, answer=None):
        self.answer = answer or plan()
        self.prompts = []

    async def run_custom(self, prompt, options, timeout=0, sem=None):
        self.prompts.append(prompt)
        return self.answer


class FakeAdo:
    def __init__(self, types=("Product Backlog Item", "Task", "Bug", "Feature"), projects=("Alpha", "Beta")):
        self.types, self.projects = list(types), list(projects)
        self.created, self.deleted, self.fail_on, self.next = [], [], None, 100

    async def list_projects(self, org):
        return [{"name": p} for p in self.projects]

    async def list_work_item_types(self, org, project):
        return self.types

    async def create_work_item(self, org, project, wtype, title, **kw):
        if self.fail_on == len(self.created) + 1:
            raise AzureDevOpsError("Azure DevOps refused it: bad field", 400)
        self.next += 1
        self.created.append({"id": self.next, "type": wtype, "title": title, "project": project, **kw})
        return {"id": self.next}

    async def delete_work_item(self, org, project, work_item_id):
        self.deleted.append(work_item_id)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------- wake word
@pytest.mark.parametrize("text", ["Claude, create a PBI for login", "hey Claude can you add a task", "Claud create a bug", "Cloud, create a PBI", "cloud please add a task for this", "Clod create a story"])
def test_wake_word_hears_claude_and_its_mishearings(text):
    assert WAKE.search(text)


@pytest.mark.parametrize("text", ["we are moving to the cloud next quarter", "the cloud migration is hard", "cloudflare is down", "I created a PBI yesterday"])
def test_normal_talk_does_not_wake_it(text):
    assert not WAKE.search(text)


# ---------------------------------------------------------------------------- the plan
def test_plan_is_checked_and_capped():
    p = parse_plan(plan())
    assert p["items"][0]["title"] == "Login page" and [t["hours"] for t in p["items"][0]["tasks"]] == [4.0, None]
    assert parse_plan(plan(confidence=0.3)) is None and parse_plan('{"intent": "none"}') is None and parse_plan("no json") is None
    many = [{"type": "task", "title": f"T{i}", "tasks": [{"title": f"s{n}", "hours": 999} for n in range(20)]} for i in range(9)]
    capped = parse_plan(plan(items=many))
    assert len(capped["items"]) == 5 and len(capped["items"][0]["tasks"]) == 12 and capped["items"][0]["tasks"][0]["hours"] is None  # absurd hours dropped
    assert parse_plan(plan(items=[{"type": "delete_everything", "title": "x"}, {"type": "pbi", "title": ""}])) is None
    assert parse_plan(plan(items=[{"type": "pbi", "title": "x" * 500}]))["items"][0]["title"] == "x" * 120


def test_type_names_follow_the_projects_process():
    scrum, agile, basic = ["Product Backlog Item", "Task", "Bug"], ["User Story", "Task", "Bug", "Epic"], ["Issue", "Task", "Epic"]
    assert map_type("pbi", scrum) == "Product Backlog Item" and map_type("pbi", agile) == "User Story" and map_type("pbi", basic) == "Issue"
    assert map_type("bug", basic) == "Issue" and map_type("feature", agile) == "Epic" and map_type("task", ["bug"]) is None


# ---------------------------------------------------------------------------- the agent
def make(answer=None, **kw):
    agent = MeetingAgent(StubClaude(answer), "sonnet", settle=0)
    m = agent.create("alice", "Sprint planning", "dtek", kw.get("project", "alpha"))
    return agent, m


async def settle(agent):
    await asyncio.gather(*list(agent._tasks))


def test_a_request_creates_the_pbi_with_linked_subtasks():
    async def go():
        agent, m = make()
        ado = FakeAdo()
        events = []
        q = agent.subscribe(m["id"])
        await agent.add_line("alice", m["id"], "Ravi", "We need a login page for the mobile app.", ado)
        assert not agent._tasks  # ordinary talk starts nothing
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI for that and add subtasks for design and build.", ado)
        await settle(agent)
        while not q.empty():
            events.append(q.get_nowait())
        return agent, m, ado, events

    agent, m, ado, events = run(go())
    a = m["actions"][0]
    assert a["status"] == "created" and a["project"] == "Alpha" and "3 work items" in a["summary"]  # project matched ignoring case
    parent, design, build = ado.created
    assert parent["type"] == "Product Backlog Item" and parent["acceptance_criteria"] == "Works on mobile" and parent["tags"] == "Claude-Meeting"
    assert design["type"] == "Task" and design["parent_id"] == parent["id"] and design["hours"] == 4.0 and build["hours"] is None
    assert [i["parent"] for i in a["items"]] == [None, parent["id"], parent["id"]] and a["items"][0]["url"] == f"https://dev.azure.com/dtek/Alpha/_workitems/edit/{parent['id']}"
    assert [e["type"] for e in events] == ["line", "line", "action", "action"] and [e["action"]["status"] for e in events if e["type"] == "action"] == ["working", "created"]
    assert "We need a login page" in agent.claude.prompts[0]  # it saw the discussion before the request


def test_it_waits_for_the_sentences_after_the_wake_word():
    async def go():
        agent = MeetingAgent(StubClaude(), "sonnet", settle=0.05)
        m = agent.create("alice", "t", "dtek", "Alpha")
        ado = FakeAdo()
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI", ado)
        await agent.add_line("alice", m["id"], "Me", "the title is Invoice export", ado)  # said right after
        await settle(agent)
        return agent

    assert "the title is Invoice export" in run(go()).claude.prompts[0]


def test_unclear_requests_and_chatter_do_nothing():
    async def go():
        agent, m = make('{"intent": "none"}')
        ado = FakeAdo()
        await agent.add_line("alice", m["id"], "Me", "Claude is a funny name for an assistant.", ado)
        await settle(agent)
        return m, ado

    m, ado = run(go())
    assert m["actions"] == [] and ado.created == []


def test_same_request_heard_twice_is_created_once():
    async def go():
        agent, m = make()
        ado = FakeAdo()
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI for login.", ado)
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI for login.", ado)  # the recogniser repeats itself
        await settle(agent)
        await agent.add_line("alice", m["id"], "Me", "Claude create the login PBI again", ado)
        await settle(agent)
        return m, ado

    m, ado = run(go())
    assert len(ado.created) == 3 and len(m["actions"]) == 1  # one PBI + two tasks, once


def test_unknown_or_missing_project_is_reported_and_nothing_is_created():
    async def go(project, plan_project):
        agent, m = make(plan(project=plan_project), project=project)
        ado = FakeAdo()
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI", ado)
        await settle(agent)
        return m, ado

    m, ado = run(go("", "Nope"))
    assert m["actions"][0]["status"] == "failed" and "“Nope” was not found" in m["actions"][0]["summary"] and ado.created == []
    m, ado = run(go("", ""))
    assert "No project is chosen" in m["actions"][0]["summary"] and ado.created == []
    m, ado = run(go("Alpha", "beta"))  # the project spoken in the request wins over the meeting's default
    assert m["actions"][0]["project"] == "Beta"


def test_a_failure_halfway_keeps_what_was_made_and_it_can_be_undone():
    async def go():
        agent, m = make()
        ado = FakeAdo()
        ado.fail_on = 3  # the second task is refused
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI and tasks", ado)
        await settle(agent)
        before = dict(m["actions"][0])
        undone = await agent.undo("alice", m["id"], m["actions"][0]["id"], ado)
        again = await agent.undo("alice", m["id"], m["actions"][0]["id"], ado)
        return before, undone, again, ado, agent, m

    before, undone, again, ado, agent, m = run(go())
    assert before["status"] == "failed" and "2 already created" in before["summary"] and "bad field" in before["error"]
    assert undone["status"] == "undone" and again is None
    assert ado.deleted == [102, 101]  # the task first, then its PBI
    assert agent.get("bob", m["id"]) is None and run(agent.undo("bob", m["id"], undone["id"], ado)) is None  # not someone else's to undo


def test_limits_and_ended_meetings():
    async def go():
        agent, m = make()
        ado = FakeAdo()
        import time

        m["recent"] = [time.time()] * 6
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI", ado)
        await settle(agent)
        agent.end("alice", m["id"])
        return m, ado, await agent.add_line("alice", m["id"], "Me", "Claude late", ado)

    m, ado, late = run(go())
    assert m["actions"][0]["status"] == "skipped" and "Too many requests" in m["actions"][0]["summary"] and ado.created == [] and late is None


def test_meetings_are_private_and_bounded(monkeypatch):
    from app.services import meeting_agent

    monkeypatch.setattr(meeting_agent, "MAX_MEETINGS_PER_OWNER", 8)
    agent = MeetingAgent(StubClaude(), settle=0)
    first = agent.create("alice", "one", "dtek", "")
    for i in range(10):
        agent.create("alice", f"m{i}", "dtek", "")
    assert agent.get("alice", first["id"]) is None and len(agent.list("alice")) == 8  # oldest dropped: memory stays small
    mine = agent.list("alice")[0]["id"]
    assert agent.get("bob", mine) is None and agent.list("bob") == [] and not agent.delete("bob", mine) and agent.delete("alice", mine)


# ---------------------------------------------------------------------------- the Azure DevOps writes
def ado_client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def token():
        return "tok"

    return AzureDevOpsClient(http, token, api_version="7.1", backoff_base=0)


def test_ado_create_sends_a_proper_patch_with_parent_link_and_tag():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"id": 7})

    async def go():
        c = ado_client(handler)
        await c.create_work_item("dtek", "My Project", "Task", "Build it", description="a <b> & c\nline2", hours=3, parent_id=5, tags="Claude-Meeting", acceptance_criteria="ignored for tasks")
        await c.create_work_item("dtek", "My Project", "Product Backlog Item", "PBI", acceptance_criteria="AC")
        await c.delete_work_item("dtek", "My Project", 7)

    run(go())
    task, pbi, delete = seen
    assert task.method == "POST" and "/dtek/My%20Project/_apis/wit/workitems/$Task" in str(task.url) and task.headers["content-type"] == "application/json-patch+json"
    assert task.headers["authorization"] == "Bearer tok"
    ops = {o["path"]: o["value"] for o in json.loads(task.content)}
    assert ops["/fields/System.Title"] == "Build it" and ops["/fields/System.Description"] == "a &lt;b&gt; &amp; c<br>line2"
    assert ops["/fields/Microsoft.VSTS.Scheduling.OriginalEstimate"] == 3 and ops["/fields/System.Tags"] == "Claude-Meeting"
    assert "/fields/Microsoft.VSTS.Common.AcceptanceCriteria" not in ops  # tasks have none
    assert ops["/relations/-"] == {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "https://dev.azure.com/dtek/_apis/wit/workItems/5"}
    assert "workitems/$Product%20Backlog%20Item" in str(pbi.url) and "/fields/Microsoft.VSTS.Common.AcceptanceCriteria" in {o["path"] for o in json.loads(pbi.content)}
    assert delete.method == "DELETE" and "/dtek/My%20Project/_apis/wit/workitems/7" in str(delete.url) and "destroy" not in str(delete.url)  # recycle bin, not destroyed


def test_ado_writes_are_not_retried_and_show_the_reason():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(400, json={"message": "TF401320: Rule Error for field Title."}) if len(calls) == 1 else httpx.Response(503)

    async def go():
        c = ado_client(handler)
        with pytest.raises(AzureDevOpsError) as e1:
            await c.create_work_item("dtek", "P", "Task", "x")
        with pytest.raises(AzureDevOpsError) as e2:
            await c.create_work_item("dtek", "P", "Task", "x")
        return e1.value, e2.value

    e1, e2 = run(go())
    assert "TF401320" in e1.message and e1.status_code == 400
    assert len(calls) == 2 and e2.status_code == 502  # a 503 is not retried: a retried create could be a duplicate


# ---------------------------------------------------------------------------- the HTTP routes
def test_routes(monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth.dependencies import get_ado_client, get_current_session
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("TRENDS_ENABLED", "false")
    monkeypatch.delenv("MEETING_AGENT_ENABLED", raising=False)
    get_settings.cache_clear()
    app = create_app()
    who = {"id": "u1"}
    ado = FakeAdo()
    app.dependency_overrides[get_current_session] = lambda: SimpleNamespace(user=SimpleNamespace(object_id=who["id"], email=None, name="N"))
    app.dependency_overrides[get_ado_client] = lambda: ado
    h = {"Origin": "http://localhost:5173"}
    with TestClient(app) as client:
        # off by default: it writes to Azure DevOps, so it must be switched on on purpose
        assert client.get("/api/meetings/status").json()["enabled"] is False
        assert client.get("/api/meetings").status_code == 404
        app.state.meetings = MeetingAgent(StubClaude(), settle=0)
        assert client.get("/api/meetings/status").json()["enabled"] is True

        m = client.post("/api/meetings", json={"title": "Sync", "organization": "dtek", "project": "Alpha"}, headers=h).json()
        mid = m["id"]
        assert client.post("/api/meetings", json={"organization": "bad org!"}, headers=h).status_code == 422
        assert client.post("/api/meetings", json={"organization": "dtek"}, headers={"Origin": "http://evil.example"}).status_code == 403
        assert client.post(f"/api/meetings/{mid}/lines", json={"speaker": "Me", "text": "Claude, create a PBI with subtasks"}, headers=h).status_code == 200
        assert client.post(f"/api/meetings/{mid}/lines", json={"text": ""}, headers=h).status_code == 422
        for _ in range(80):
            got = client.get(f"/api/meetings/{mid}").json()
            if got["actions"] and got["actions"][0]["status"] != "working":
                break
            client.portal.call(asyncio.sleep, 0.05)
        action = got["actions"][0]
        assert action["status"] == "created" and len(ado.created) == 3 and len(got["lines"]) == 1
        assert "Claude, create a PBI" in client.get(f"/api/meetings/{mid}/export").text and "Product Backlog Item" in client.get(f"/api/meetings/{mid}/export").text
        # someone else sees nothing
        who["id"] = "u2"
        assert client.get("/api/meetings").json() == []
        assert client.get(f"/api/meetings/{mid}").status_code == 404
        assert client.post(f"/api/meetings/{mid}/actions/{action['id']}/undo", headers=h).status_code == 404
        who["id"] = "u1"
        undone = client.post(f"/api/meetings/{mid}/actions/{action['id']}/undo", headers=h).json()
        assert undone["status"] == "undone" and ado.deleted == [102, 103, 101] or sorted(ado.deleted) == [101, 102, 103]
        assert client.post(f"/api/meetings/{mid}/actions/{action['id']}/undo", headers=h).status_code == 404
        assert client.post(f"/api/meetings/{mid}/project", json={"project": "Beta"}, headers=h).json() == {"project": "Beta"}
        assert client.post(f"/api/meetings/{mid}/end", headers=h).json() == {"ended": True}
        assert client.post(f"/api/meetings/{mid}/lines", json={"text": "late"}, headers=h).status_code == 409
        assert client.delete(f"/api/meetings/{mid}", headers=h).json() == {"deleted": True}
        assert client.get(f"/api/meetings/{mid}").status_code == 404


def test_two_requests_close_together_are_both_handled_and_not_repeated():
    class TwoPlans(StubClaude):
        async def run_custom(self, prompt, options, timeout=0, sem=None):
            self.prompts.append(prompt)
            asked = prompt.split("the one in line", 1)[1].split("Requests made earlier", 1)[0]
            if "bug" in asked:
                return plan(items=[{"type": "bug", "title": "Checkout crashes on Android", "description": "", "acceptance_criteria": "", "tasks": []}])
            return plan()

    async def go():
        agent = MeetingAgent(TwoPlans(), "sonnet", settle=0.05)
        m = agent.create("alice", "t", "dtek", "Alpha")
        ado = FakeAdo()
        await agent.add_line("alice", m["id"], "Me", "Claude, create a PBI for the login page with subtasks", ado)
        await agent.add_line("alice", m["id"], "Me", "Cloud, create a bug for the checkout crash", ado)  # said a moment later
        await settle(agent)
        return agent, m, ado

    agent, m, ado = run(go())
    assert [a["status"] for a in m["actions"]] == ["created", "created"]
    assert [c["title"] for c in ado.created] == ["Login page", "Design", "Build", "Checkout crashes on Android"]  # nothing repeated, nothing lost
    assert "Already created in this meeting: Login page; Design; Build" in agent.claude.prompts[1]


def test_meetings_are_saved_encrypted_and_survive_a_restart(tmp_path):
    from app.services.meeting_store import MeetingStore

    async def first():
        agent = MeetingAgent(StubClaude(), settle=0, store=MeetingStore(tmp_path, "secret"))
        m = agent.create("alice@x.com", "Sprint demo", "dtek", "Alpha", source="teams")
        m["join_url"] = "https://teams.microsoft.com/l/meetup-join/abc"
        await agent.add_line("alice@x.com", m["id"], "Ravi", "a very private sentence", None)
        agent.end("alice@x.com", m["id"])
        agent.flush_now()
        return m["id"]

    mid = run(first())
    raw = b"".join(f.read_bytes() for f in tmp_path.rglob("*.enc"))
    assert raw and b"private sentence" not in raw and b"Sprint demo" not in raw  # nothing readable on disk

    again = MeetingAgent(StubClaude(), settle=0, store=MeetingStore(tmp_path, "secret"))  # "the server restarted"
    assert [m["title"] for m in again.list("alice@x.com")] == ["Sprint demo"]
    saved = again.get("alice@x.com", mid)
    assert saved["lines"][0]["text"] == "a very private sentence" and saved["ended_at"] and saved["stage"] == ""
    assert again.by_join_url("alice@x.com") == {"https://teams.microsoft.com/l/meetup-join/abc": mid}
    assert again.list("bob@x.com") == [] and again.get("bob@x.com", mid) is None  # private to the owner
    assert MeetingAgent(StubClaude(), store=MeetingStore(tmp_path, "other-secret")).list("alice@x.com") == []  # wrong key: unreadable, not shown
    assert again.delete("alice@x.com", mid) and not list(tmp_path.rglob("*.enc"))  # delete removes the file too


def test_old_saved_meetings_are_deleted_and_bad_ids_are_refused(tmp_path):
    import os
    import time

    from app.services.meeting_store import MeetingStore

    store = MeetingStore(tmp_path, "s", retention_days=1)
    agent = MeetingAgent(StubClaude(), settle=0, store=store)
    m = agent.create("a@x.com", "old", "dtek", "")
    agent.flush_now()
    path = next(tmp_path.rglob("*.enc"))
    os.utime(path, (time.time() - 3 * 86400,) * 2)
    assert MeetingAgent(StubClaude(), store=store).list("a@x.com") == [] and not path.exists()
    for bad in ("../../etc/passwd", "", "x" * 32, m["id"].upper()):
        assert store.delete("a@x.com", bad) is False
