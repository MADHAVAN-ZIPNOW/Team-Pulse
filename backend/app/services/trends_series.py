"""Real search-interest curves (the up-and-down line) from Google Trends' own "interest over time" service.

Two small requests per trend (about a second): Explore gives a token, then the multiline widget gives the points.
Values are Google's 0-100 scale, relative to that trend's own peak in the window. Curves are kept in memory only.
When Google throttles (HTTP 429) the client backs off for a minute and the page falls back to a plain timeline bar.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger("pr_assistant.trends")

EXPLORE = "https://trends.google.com/trends/api/explore"
MULTILINE = "https://trends.google.com/trends/api/widgetdata/multiline"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
# Google's "now" windows: there is no 48-hour one, so 48 h uses the 7-day curve cropped to the last 48 hours.
TIME = {4: "now 4-H", 24: "now 1-d", 48: "now 7-d", 168: "now 7-d"}
MAX_POINTS = 72
CACHE_SECONDS = 600
Get = Callable[[str, dict[str, str]], Awaitable[str]]


class Throttled(Exception):
    """Google asked us to slow down."""


def strip_prefix(text: str) -> Any:
    """Google prefixes JSON with )]}' to stop it being read as a script."""
    return json.loads(text[text.index("\n") + 1 :] if text.startswith(")]}'") else text)


def downsample(values: list[int], n: int = MAX_POINTS) -> list[int]:
    if len(values) <= n:
        return values
    size = len(values) / n
    return [max(values[int(i * size) : max(int((i + 1) * size), int(i * size) + 1)]) for i in range(n)]


def parse_series(data: Any, hours: int, now: float | None = None) -> dict[str, Any] | None:
    rows = ((data or {}).get("default") or {}).get("timelineData") or []
    cutoff = (now if now is not None else datetime.now(timezone.utc).timestamp()) - hours * 3600
    pts = []
    for r in rows:
        try:
            t = int(r["time"])
            v = int((r.get("value") or [0])[0]) if r.get("hasData", [True])[0] else 0
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if t >= cutoff:
            pts.append((t, max(0, min(100, v))))
    if len(pts) < 3:
        return None
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"t0": iso(pts[0][0]), "t1": iso(pts[-1][0]), "values": downsample([v for _, v in pts])}


class SeriesClient:
    def __init__(self, get: Get | None = None) -> None:
        self._get_fn = get
        self._http: httpx.AsyncClient | None = None
        self._ready = asyncio.Lock()
        self._sem = asyncio.Semaphore(3)  # Google is touchy: at most three at a time
        self._blocked_until = 0.0
        self._cache: dict[tuple[str, int, str], tuple[float, dict[str, Any] | None]] = {}

    async def aclose(self) -> None:
        if self._http:
            await self._http.aclose()

    async def _get(self, url: str, params: dict[str, str]) -> str:
        if self._get_fn:
            return await self._get_fn(url, params)
        async with self._ready:
            if self._http is None:
                self._http = httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                                               timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=True)
                await self._http.get("https://trends.google.com/")  # sets the cookie Google expects
        res = await self._http.get(url, params=params)
        if res.status_code == 429:
            raise Throttled()
        res.raise_for_status()
        return res.text

    async def series(self, keyword: str, geo: str, hours: int) -> dict[str, Any] | None:
        """{"t0", "t1", "values"} or None when Google has nothing / is throttling."""
        loop = asyncio.get_running_loop()
        key = (geo, hours, keyword.casefold())
        hit = self._cache.get(key)
        if hit and loop.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        if loop.time() < self._blocked_until:
            return None
        try:
            async with self._sem:
                req = {"comparisonItem": [{"keyword": keyword, "geo": "" if geo == "WW" else geo, "time": TIME.get(hours, "now 1-d")}],
                       "category": 0, "property": ""}
                explore = strip_prefix(await self._get(EXPLORE, {"hl": "en-US", "tz": "0", "req": json.dumps(req)}))
                widget = next(w for w in explore["widgets"] if w.get("id") == "TIMESERIES")
                data = strip_prefix(await self._get(MULTILINE, {"hl": "en-US", "tz": "0", "req": json.dumps(widget["request"]), "token": widget["token"]}))
            result = parse_series(data, hours)
        except Throttled:
            self._blocked_until = loop.time() + 60
            logger.warning("Google Trends is throttling interest curves; pausing them for a minute")
            return None
        except (httpx.HTTPError, ValueError, KeyError, StopIteration, TypeError) as exc:
            logger.info("No interest curve for %r (%s)", keyword, type(exc).__name__)
            return None
        self._cache[key] = (loop.time(), result)
        return result
