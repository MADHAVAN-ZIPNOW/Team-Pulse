import asyncio
from types import SimpleNamespace

import httpx

from app.services.graph_teams import GraphTeamsClient
from app.services.meeting_agent import MeetingAgent

VIDEO = bytes(range(256)) * 40  # 10,240 bytes standing in for a recording


class Chunks(httpx.AsyncByteStream):
    """A response body that is streamed in pieces, like a real download (and unlike a body that is read in advance)."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        for i in range(0, len(self.data), 4096):
            yield self.data[i : i + 4096]


def graph_with(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def token():
        return "tok"

    return GraphTeamsClient(http, token)


def test_recording_opens_as_a_stream_with_the_range_and_without_leaking_the_token():
    seen = []

    def handler(req):
        seen.append(req)
        if "graph.microsoft.com" in str(req.url):
            return httpx.Response(302, headers={"Location": "https://contoso.sharepoint.com/download?tempauth=abc"})
        start = int(req.headers["range"].split("=")[1].split("-")[0]) if "range" in req.headers else 0
        body = VIDEO[start:]
        return httpx.Response(206 if "range" in req.headers else 200, stream=Chunks(body), headers={
            "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{len(VIDEO) - 1}/{len(VIDEO)}"})

    async def go():
        g = graph_with(handler)
        res = await g.open_recording("meet/1", "rec 1", "bytes=1000-")
        data = await res.aread()
        await res.aclose()
        return res, data

    res, data = asyncio.run(go())
    assert res.status_code == 206 and data == VIDEO[1000:]
    first, second = seen
    assert first.headers["authorization"] == "Bearer tok" and first.headers["range"] == "bytes=1000-"
    assert "/me/onlineMeetings/meet%2F1/recordings/rec%201/content" in str(first.url)
    assert "authorization" not in second.headers  # the Microsoft token is never sent to the download server


def test_microsoft_refusing_is_explained():
    import pytest

    from app.services.graph_teams import GraphError

    async def go():
        with pytest.raises(GraphError) as e:
            await graph_with(lambda req: httpx.Response(403)).open_recording("m", "r")
        return e.value

    err = asyncio.run(go())
    assert err.status_code == 403 and "organizer" in err.message


def test_only_the_owner_can_watch_it_and_it_plays_in_pieces(monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth.dependencies import get_current_session
    from app.config import get_settings
    from app.main import create_app
    from app.routes import meeting_routes

    monkeypatch.setenv("TRENDS_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    who = {"id": "u1"}
    app.dependency_overrides[get_current_session] = lambda: SimpleNamespace(user=SimpleNamespace(object_id=who["id"], email=None, name="N"))

    def handler(req):
        if "graph.microsoft.com" in str(req.url):
            return httpx.Response(302, headers={"Location": "https://contoso.sharepoint.com/file"})
        rng = req.headers.get("range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            return httpx.Response(206, stream=Chunks(VIDEO[start:]), headers={"Content-Type": "video/mp4", "Content-Range": f"bytes {start}-{len(VIDEO) - 1}/{len(VIDEO)}"})
        return httpx.Response(200, stream=Chunks(VIDEO), headers={"Content-Type": "video/mp4", "Content-Length": str(len(VIDEO))})

    app.dependency_overrides[meeting_routes._graph] = lambda: graph_with(handler)
    with TestClient(app) as client:
        agent = MeetingAgent(SimpleNamespace(), settle=0)
        app.state.meetings = agent
        plain = agent.create("u1", "No recording", "dtek", "")
        with_rec = agent.create("u1", "Sprint demo", "dtek", "")
        agent.set_recording("u1", with_rec["id"], "om1", "rec1")
        assert agent.public(with_rec)["hasVideo"] is True and agent.public(plain)["hasVideo"] is False
        assert [m["hasVideo"] for m in agent.list("u1")] == [False, True] or {m["hasVideo"] for m in agent.list("u1")} == {True, False}

        whole = client.get(f"/api/meetings/{with_rec['id']}/video")
        assert whole.status_code == 200 and whole.content == VIDEO and whole.headers["accept-ranges"] == "bytes"
        part = client.get(f"/api/meetings/{with_rec['id']}/video", headers={"Range": "bytes=5000-"})
        assert part.status_code == 206 and part.content == VIDEO[5000:] and part.headers["content-range"].startswith("bytes 5000-")  # skipping ahead works
        assert client.get(f"/api/meetings/{plain['id']}/video").status_code == 404
        who["id"] = "someone-else"
        assert client.get(f"/api/meetings/{with_rec['id']}/video").status_code == 404  # not theirs
