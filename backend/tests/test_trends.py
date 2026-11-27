import json

import pytest

from app.config import _parse_regions
from app.services import trends_service
from app.services.trends_service import TrendsService, merge_countries, parse_feed, public, select, stats
from app.services.trends_source import SourceError, parse_page, traffic_number


def _page(rows):
    return "<html><script>AF_initDataCallback({key: 'ds:0', hash: '1', data:" + json.dumps([None, rows]) + ", sideChannel: {}});</script></html>"


def _row(title, start, end=None, volume=200000, pct=1000, cat=17, geo="IN"):
    return [title, None, geo, [start], [end] if end else None, None, volume, None, pct, [title, "related one", "related two"], [cat], [], title]


NOW = "2026-11-25T12:00:00Z"
ROWS = [
    _row("Real Madrid vs PSV", 1795543800),
    _row("Old match", 1795500000, 1795510000, 5000, 0),
    _row("Stock market", 1795550000, None, 50000, 500, cat=3),
]


def test_parse_page_reads_every_field():
    items = parse_page(_page(ROWS), "IN", now=NOW)
    assert [i["title"] for i in items] == ["Real Madrid vs PSV", "Old match", "Stock market"]  # Google's order
    rm = items[0]
    assert (rm["traffic"], rm["increase"], rm["categoryId"], rm["endedAt"]) == ("200K+", "1,000%", 17, None)
    assert items[1]["endedAt"] is not None and items[1]["endedAt"] > items[1]["startedAt"]
    assert rm["news"][0]["url"].startswith("https://www.google.com/search?q=") and rm["related"][:2] == ["Real Madrid vs PSV", "related one"]


def test_parse_page_fails_clearly_when_layout_changes():
    with pytest.raises(SourceError):
        parse_page("<html>nothing here</html>", "IN")


def test_select_filters_sorts_and_pages():
    items = parse_page(_page(ROWS), "IN", now=NOW)
    for i in items:
        i["category"] = {17: "Sports", 3: "Business"}[i["categoryId"]]
    live, total = select(items)
    assert total == 2 and [i["title"] for i in live] == ["Real Madrid vs PSV", "Stock market"]
    assert select(items, status="ended")[1] == 1 and select(items, status="all")[1] == 3
    assert [i["title"] for i in select(items, category="Business")[0]] == ["Stock market"]
    assert select(items, text="related two")[1] == 2  # also searches the related searches
    assert [i["title"] for i in select(items, sort="traffic")[0]] == ["Real Madrid vs PSV", "Stock market"]
    assert [i["title"] for i in select(items, status="all", sort="oldest", limit=1)[0]] == ["Old match"]
    assert select(items, status="all", offset=2, limit=5)[0][0]["title"] == "Stock market"


def test_worldwide_merges_same_trend_across_countries():
    india = parse_page(_page([_row("Cricket", 1795500000, volume=500000), _row("Local", 1795500000)]), "IN", now=NOW)
    us = parse_page(_page([_row("cricket", 1795400000, volume=1000000, geo="US")]), "US", now=NOW)
    merged = merge_countries([india, us])
    assert len(merged) == 2
    top = select(merged)[0][0]
    assert sorted(top["countries"]) == ["IN", "US"] and top["traffic"] == "1M+" and top["geo"] == "WW"
    assert top["startedAt"] == min(india[0]["startedAt"], us[0]["startedAt"])
    assert public(top)["countries"] == top["countries"]


def test_stats():
    items = parse_page(_page(ROWS), "IN", now=NOW)
    for i in items:
        i["category"] = "Sports"
    st = stats(items)
    assert st["active"] == 2 and st["topTrend"] == {"title": "Real Madrid vs PSV", "traffic": "200K+"}
    assert st["risingTrend"]["increase"] == "1,000%" and st["categories"] == [{"name": "Sports", "count": 2}]


def test_parse_feed_fallback_has_same_shape():
    out = parse_feed('x {"trends": [{"title": " X ", "traffic": "2K+", "news": [{"title": "n", "url": "javascript:1"}, {"title": "ok", "url": "https://a.b"}]}, {"title": ""}]} y', "IN")
    assert len(out) == 1 and out[0]["title"] == "X" and out[0]["volume"] == 2000 and out[0]["news"] == [{"title": "ok", "url": "https://a.b"}]
    assert out[0]["endedAt"] is None and out[0]["category"] == "Other"
    with pytest.raises(ValueError):
        parse_feed('{"nope": 1}', "IN")


def test_misc_helpers():
    assert traffic_number("200K+") == 200_000 and traffic_number("2M+") == 2_000_000 and traffic_number("1,000%") == 1000
    assert _parse_regions("in, us;gb xx1 ZZ") == ("IN", "US", "GB", "ZZ")
    assert len(_parse_regions("")) >= 3


class StubClaude:
    """Stands in for Claude: names categories, researches a trend, reads an old date."""

    def __init__(self):
        self.calls = []

    async def run_custom(self, prompt, options, timeout=0, sem=None):
        self.calls.append(prompt[:40])
        if "category NUMBER" in prompt:
            return '{"names": {"17": "Sports", "3": "Business & Finance"}}'
        if "Research why" in prompt:
            return '{"headline": "H", "analysis": "Because.", "keyPoints": ["k"], "sources": []}'
        if "Find what was trending" in prompt:
            return ('{"fromGoogle": true, "note": "from Year in Search", "source": {"title": "YIS", "url": "https://trends.withgoogle.com/"},'
                    ' "trends": [{"title": "Old thing", "category": "Sports", "traffic": "1M+", "summary": "s", "url": "https://x.y/z"}]}')
        raise AssertionError("unexpected prompt")


def _app(monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth.dependencies import get_current_session
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("TRENDS_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_current_session] = lambda: object()
    calls = {"fetch": 0}

    async def fake_fetch(geo, hours=24):
        calls["fetch"] += 1
        extra = [_row(f"Extra {n}", 1795540000, None, 1000, 0, geo=geo) for n in "ABC"]
        return _page([_row("Real Madrid vs PSV", 1795543800, geo=geo), _row("Old match", 1795500000, 1795510000, 5000, 0, geo=geo), _row("Stock market", 1795550000, None, 50000, 500, cat=3, geo=geo), *extra])

    monkeypatch.setattr(trends_service, "fetch_page", fake_fetch)
    stub = StubClaude()
    svc = TrendsService(stub, ("IN", "US"), "haiku", 180, "sonnet")
    return TestClient(app), app, svc, stub, calls


def test_routes_are_live_and_in_memory(monkeypatch, tmp_path):
    client, app, svc, stub, calls = _app(monkeypatch)
    with client:
        app.state.trends = svc
        h = {"Origin": "http://localhost:5173"}
        meta = client.get("/api/trends/meta").json()
        assert "FR" in meta["countries"] and meta["windows"] == [4, 24, 48, 168]
        r = client.get("/api/trends?geo=IN").json()
        assert r["total"] == 5 and r["items"][0]["title"] == "Real Madrid vs PSV" and r["items"][0]["category"] == "Sports"
        assert r["categories"] == ["Business & Finance", "Sports"]
        assert client.get("/api/trends?geo=IN&status=ended").json()["items"][0]["title"] == "Old match"
        assert client.get("/api/trends/stats?geo=IN").json()["active"] == 5
        assert calls["fetch"] == 1  # all of the above came from one read of Google (kept in memory)
        client.post("/api/trends/refresh?geo=IN", headers=h)
        assert calls["fetch"] == 2  # refresh really re-reads Google
        world = client.get("/api/trends?geo=WW&limit=1").json()
        assert world["total"] == 5 and sorted(world["items"][0]["countries"]) == ["IN", "US"]
        assert client.get("/api/trends?geo=ZZ").status_code == 404
        assert client.get("/api/trends?geo=IN&hours=5").status_code == 422
        assert client.get("/api/trends?geo=IN&offset=1&limit=1").json()["items"][0]["title"] == "Stock market"
        first = client.get("/api/trends?geo=IN&limit=1500&status=all").json()["items"][0]
        assert first["volume"] == 200000 and first["pos"] == 0 and first["increaseValue"] == 1000 and "related one" in first["related"]
    assert not list(tmp_path.iterdir())  # nothing written anywhere


def test_past_runs_in_background_and_caches(monkeypatch):
    import asyncio

    client, app, svc, stub, _ = _app(monkeypatch)
    h = {"Origin": "http://localhost:5173"}
    with client:
        app.state.trends = svc

        def until_ready(url):
            for _ in range(50):
                body = client.post(url, headers=h).json()
                if body["status"] != "loading":
                    return body
                client.portal.call(asyncio.sleep, 0.05)
            raise AssertionError("never finished")

        p = until_ready("/api/trends/past?geo=IN&from=2024-03-05&to=2024-03-05")
        assert p["status"] == "ready" and p["items"][0]["title"] == "Old thing" and p["source"]["url"].startswith("https://") and p["fromGoogle"] is True
        assert p["items"][0]["startedAt"] == "2024-03-05T00:00:00Z"
        n = len(stub.calls)
        assert until_ready("/api/trends/past?geo=IN&from=2024-03-05&to=2024-03-05")["status"] == "ready" and len(stub.calls) == n  # cached
        assert client.post("/api/trends/past?geo=IN&from=2999-01-01&to=2999-01-02", headers=h).status_code == 422
        assert client.post("/api/trends/past?geo=IN&from=2020-01-01&to=2023-01-01", headers=h).status_code == 422  # > 1 year
        assert client.post("/api/trends/past?geo=IN&from=nope&to=nope!!!!", headers=h).status_code == 422


def test_chat_streams_status_and_text_and_knows_the_trend(monkeypatch):
    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock

    client, app, svc, stub, _ = _app(monkeypatch)
    seen = {}

    async def fake_query(prompt, options):
        seen["prompt"], seen["options"] = prompt, options
        tool = AssistantMessage.__new__(AssistantMessage)
        tool.content = [ToolUseBlock(id="1", name="WebSearch", input={"query": "real madrid psv"})]
        yield tool
        for piece in ("Real Madrid ", "host PSV."):
            yield StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}})
        final = AssistantMessage.__new__(AssistantMessage)
        final.content = [TextBlock(text="Real Madrid host PSV.")]
        yield final  # already streamed: must not be repeated
        done = ResultMessage.__new__(ResultMessage)
        done.is_error = False
        yield done

    monkeypatch.setattr(trends_service, "query", fake_query)
    h = {"Origin": "http://localhost:5173"}
    with client:
        app.state.trends = svc
        client.get("/api/trends?geo=IN")  # puts the list in memory so the chat knows the trend's numbers
        body = {"geo": "IN", "title": "Real Madrid vs PSV", "question": "Why is this trending?", "history": [{"role": "user", "text": "hi"}], "deep": True}
        r = client.post("/api/trends/chat", json=body, headers=h)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
        assert events[0] == {"type": "status", "text": "Searching the web for real madrid psv"}
        assert "".join(e["text"] for e in events if e["type"] == "delta") == "Real Madrid host PSV."
        assert events[-1]["type"] == "done"
        assert "Search volume: 200K+ (up 1,000%)" in seen["prompt"] and "User: hi" in seen["prompt"] and "Why is this trending?" in seen["prompt"]
        assert seen["options"].model == "sonnet"  # deep mode uses the research model
        assert client.post("/api/trends/chat", json={**body, "geo": "ZZ"}, headers=h).status_code == 404
        assert client.post("/api/trends/chat", json={**body, "question": ""}, headers=h).status_code == 422
        assert client.post("/api/trends/chat", json=body, headers={"Origin": "http://evil.example"}).status_code == 403  # wrong origin


def test_photo_lookup_is_fast_and_only_accepts_related_pages():
    import asyncio

    from app.services.trends_photos import Throttled, find_photo, search_queries, thumbnail

    ok = "https://upload.wikimedia.org/x/640px-a.jpg"
    assert thumbnail({"title": "Real Madrid CF", "thumbnail": {"source": ok}}, "real madrid") == {"url": ok, "title": "Real Madrid CF"}
    assert thumbnail({"title": "Unrelated", "thumbnail": {"source": ok}}, "real madrid") is None
    assert thumbnail({"title": "Real Madrid CF", "thumbnail": {"source": "https://evil.example/a.jpg"}}, "real madrid") is None
    assert thumbnail(None, "x") is None and thumbnail({"title": "Real"}, "real") is None
    assert search_queries("real madrid vs psv", ["real madrid vs psv", "champions league", "ucl"]) == ["real madrid vs psv", "real madrid", "psv", "champions league", "ucl"]
    assert search_queries("bentley", []) == ["bentley"]

    asked = []

    async def fake(url, params=None):
        asked.append(params["search"] if params else url.rsplit("/", 1)[-1])
        if params:
            return ["q", ["Real Madrid CF", "Real Madrid Castilla"]] if params["search"] == "real madrid" else ["q", []]
        return {"title": "Real Madrid CF", "thumbnail": {"source": ok}} if "CF" in url else {"title": "Real Madrid Castilla"}

    got = asyncio.run(find_photo(["real madrid vs psv", "real madrid", "later"], get=fake))
    assert got == {"url": ok, "title": "Real Madrid CF"} and "later" not in asked  # stops at the first photo

    async def throttled(url, params=None):
        raise Throttled()

    with pytest.raises(Throttled):
        asyncio.run(find_photo(["x"], get=throttled))


def test_photo_route_uses_wikipedia_then_claude_and_caches(monkeypatch):
    import asyncio

    client, app, svc, stub, _ = _app(monkeypatch)
    calls = []

    async def fake_find(queries):
        calls.append(queries)
        if queries[0] == "Real Madrid":
            return {"url": "https://upload.wikimedia.org/p.jpg", "title": "Real Madrid CF"}
        if queries[0] == "Rare thing":
            raise trends_service.Throttled()
        return None

    monkeypatch.setattr(trends_service, "find_photo", fake_find)
    orig = stub.run_custom

    async def run(prompt, options, timeout=0, sem=None):
        if "Find ONE good photo" in prompt:
            return '{"image": "https://news.example.com/img/a.jpg", "pageTitle": "A news story"}'
        return await orig(prompt, options, timeout, sem)

    stub.run_custom = run
    with client:
        app.state.trends = svc
        client.get("/api/trends?geo=IN")
        r = client.get("/api/trends/photo?title=Real%20Madrid").json()
        assert r == {"url": "https://upload.wikimedia.org/p.jpg", "title": "Real Madrid CF", "source": "Wikipedia", "pending": False}
        client.get("/api/trends/photo?title=real%20madrid")
        assert len(calls) == 1  # second ask came from memory

        def until_done(title):
            for _ in range(60):
                body = client.get(f"/api/trends/photo?title={title}").json()
                if not body["pending"]:
                    return body
                client.portal.call(asyncio.sleep, 0.05)
            raise AssertionError("never finished")

        first = client.get("/api/trends/photo?title=Nothing%20here").json()
        assert first["url"] is None and first["pending"] is True  # Wikipedia had none: Claude is searching in the background
        done = until_done("Nothing%20here")
        assert done["url"] == "https://news.example.com/img/a.jpg" and done["source"] == "news.example.com"
        assert until_done("Rare%20thing")["url"] == "https://news.example.com/img/a.jpg"  # throttled Wikipedia -> Claude fallback


def test_busy_google_keeps_the_last_list_and_does_not_call_claude(monkeypatch):
    import asyncio

    stub = StubClaude()
    state = {"fail": False}

    async def fake_fetch(geo, hours=24):
        if state["fail"]:
            raise SourceError("Google Trends answered HTTP 429.")
        return _page([_row(f"Trend {n}", 1795540000, None, 1000 * (n + 1), 0) for n in range(6)])

    monkeypatch.setattr(trends_service, "fetch_page", fake_fetch)

    async def go():
        svc = TrendsService(stub, ("IN",), "haiku", 180, "sonnet")
        first = await svc.snapshot("IN", 24)
        state["fail"] = True
        again = await svc.snapshot("IN", 24, force=True)  # Google says 429
        return first, again, stub.calls

    first, again, calls = asyncio.run(go())
    assert again is first and again.stale and len(again.items) == 6
    assert not any("RSS feed" in c or "collect the current" in c for c in calls)  # no slow Claude fallback while we have a list


def test_interest_curve_parsing_and_client():
    import asyncio

    from app.services.trends_series import SeriesClient, Throttled, downsample, parse_series, strip_prefix

    assert strip_prefix(")]}'\n{\"a\": 1}") == {"a": 1} and strip_prefix('{"a": 2}') == {"a": 2}
    assert downsample(list(range(10)), 20) == list(range(10)) and len(downsample(list(range(180)), 72)) == 72
    assert max(downsample([0] * 100 + [100] + [0] * 79, 72)) == 100  # a spike is never averaged away

    now = 1_795_600_000
    rows = [{"time": str(now - 3600 * h), "value": [v], "hasData": [True]} for h, v in [(30, 5), (10, 20), (5, 100), (1, 40), (0, 10)]]
    cur = parse_series({"default": {"timelineData": rows}}, 24, now=now)
    assert cur["values"] == [20, 100, 40, 10]  # the 30-hour-old point is outside the 24 h window
    assert parse_series({"default": {"timelineData": rows[:1]}}, 24, now=now) is None and parse_series({}, 24) is None

    calls = []

    async def fake(url, params):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("explore"):
            return ")]}'\n" + json.dumps({"widgets": [{"id": "TIMESERIES", "token": "T", "request": {"x": 1}}]})
        tl = [{"time": str(int(__import__("time").time()) - 600 * k), "value": [k % 7 * 10], "hasData": [True]} for k in range(30)]
        return ")]}'\n" + json.dumps({"default": {"timelineData": tl}})

    async def go():
        c = SeriesClient(get=fake)
        a = await c.series("Real Madrid", "IN", 24)
        b = await c.series("real madrid", "IN", 24)  # same trend: from memory
        return a, b

    a, b = asyncio.run(go())
    assert a is b and len(a["values"]) == 30 and calls == ["explore", "multiline"]

    async def throttled(url, params):
        raise Throttled()

    async def go2():
        c = SeriesClient(get=throttled)
        first = await c.series("x", "IN", 24)
        calls.clear()
        return first, await c.series("y", "IN", 24)  # blocked for a minute: no further requests

    assert asyncio.run(go2()) == (None, None)


def test_series_route(monkeypatch):
    client, app, svc, stub, _ = _app(monkeypatch)

    async def fake_series(keyword, geo, hours):
        return {"t0": "2026-01-01T00:00:00Z", "t1": "2026-01-01T01:00:00Z", "values": [0, 50, 100, 30]} if keyword == "ok" else None

    svc.curves.series = fake_series
    with client:
        app.state.trends = svc
        assert client.get("/api/trends/series?geo=IN&hours=24&title=ok").json()["values"] == [0, 50, 100, 30]
        assert client.get("/api/trends/series?geo=IN&hours=24&title=nope").json() == {"t0": None, "t1": None, "values": None}
        assert client.get("/api/trends/series?geo=ZZ&hours=24&title=ok").status_code == 404
        assert client.get("/api/trends/series?geo=IN&hours=5&title=ok").status_code == 422
