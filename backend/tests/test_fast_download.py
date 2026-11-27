import asyncio
import os
import re

import httpx

from app.services import graph_teams
from app.services.graph_teams import GraphError, GraphTeamsClient

DATA = os.urandom(50_000)


class Client(GraphTeamsClient):
    async def _latest(self, meeting_id, kind):  # type: ignore[no-untyped-def]
        return "rec1"


def make(handler):
    async def token() -> str:
        return "t"

    return Client(httpx.AsyncClient(transport=httpx.MockTransport(handler)), token)


def run(client, tmp_path, limit=10**9):
    dst = str(tmp_path / "rec")
    got = asyncio.run(client.download_recording("m", dst, limit))
    return got, dst


def ranged(request: httpx.Request) -> httpx.Response:
    m = re.match(r"bytes=(\d+)-(\d+)", request.headers.get("range", ""))
    if not m:
        return httpx.Response(200, content=DATA)
    a, b = int(m.group(1)), min(int(m.group(2)), len(DATA) - 1)
    return httpx.Response(206, content=DATA[a : b + 1], headers={"content-range": f"bytes {a}-{b}/{len(DATA)}"})


def test_a_recording_is_fetched_in_pieces_and_put_back_together(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_teams, "CHUNK", 7_000)
    seen = []

    def handler(request):
        seen.append(request.headers.get("range"))
        return ranged(request)

    got, dst = run(make(handler), tmp_path)
    assert got is True and open(dst, "rb").read() == DATA
    assert len([r for r in seen if r and r != "bytes=0-0"]) == 8  # 50,000 bytes in pieces of 7,000


def test_it_falls_back_to_one_connection_when_the_server_ignores_ranges(tmp_path):
    got, dst = run(make(lambda request: httpx.Response(200, content=DATA)), tmp_path)
    assert got is True and open(dst, "rb").read() == DATA


def test_no_recording_is_false_and_a_too_large_one_is_refused(tmp_path):
    assert run(make(lambda request: httpx.Response(404)), tmp_path)[0] is False
    try:
        run(make(ranged), tmp_path, limit=1000)
    except GraphError as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("expected a size error")


def test_a_piece_that_keeps_failing_gives_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_teams, "CHUNK", 20_000)

    def handler(request):
        if request.headers.get("range") == "bytes=0-0":
            return ranged(request)
        return httpx.Response(500)

    try:
        run(make(handler), tmp_path)
    except GraphError as exc:
        assert "interrupted" in exc.message
    else:
        raise AssertionError("expected an error")
