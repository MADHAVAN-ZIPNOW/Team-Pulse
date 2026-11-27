"""Fast photo lookup for a trend: Wikipedia page images (about half a second). Nothing is stored on disk.

Path 1 (fast): search Wikipedia for the trend, read the lead image of the best matching pages. A photo is only
accepted when the page title shares a word with what was searched, and the page name is shown next to it.
Path 2 (slower, in trends_service): when Wikipedia has nothing, Claude searches the web for a picture in the background.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

SEARCH = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "TeamPulseTrends/1.0 (internal trends dashboard; uses Wikipedia page images)"


class Throttled(Exception):
    """Wikipedia asked us to slow down; this is temporary, not "no photo"."""


Json = Callable[[str, dict[str, str] | None], Awaitable[Any]]


async def _get(url: str, params: dict[str, str] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), headers={"User-Agent": USER_AGENT}) as http:
        res = await http.get(url, params=params)
    if res.status_code == 429:
        raise Throttled()
    if res.status_code == 404:
        return None
    res.raise_for_status()
    return res.json()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.casefold()) if len(w) >= 3 and w not in {"vs", "the", "and", "fc"}}


def thumbnail(summary: Any, query: str) -> dict[str, str] | None:
    """The page's lead image, if it is on Wikimedia and the page is about something in the query."""
    if not isinstance(summary, dict):
        return None
    url = (summary.get("thumbnail") or {}).get("source", "")
    title = str(summary.get("title") or "")
    if url.startswith("https://upload.wikimedia.org/") and _words(query) & _words(title):
        return {"url": url, "title": title}
    return None


def search_queries(title: str, related: list[str]) -> list[str]:
    """'real madrid vs psv' is not a page, but 'real madrid' and 'psv' are: try the whole title, each side, then related searches."""
    parts = [p for p in re.split(r"\s+(?:vs\.?|v|versus|x)\s+", title, flags=re.I) if p] if re.search(r"\s(vs\.?|v|versus)\s", title, re.I) else []
    out: list[str] = []
    for q in [title, *parts, *related[1:3]]:
        q = q.strip()
        if q and q.casefold() not in {o.casefold() for o in out}:
            out.append(q)
    return out[:5]


async def find_photo(queries: list[str], get: Json = _get) -> dict[str, str] | None:
    """First usable photo for the queries. Raises Throttled when Wikipedia is rate limiting us."""
    for q in queries:
        found = await get(SEARCH, {"action": "opensearch", "search": q, "limit": "3", "format": "json"})
        titles = found[1] if isinstance(found, list) and len(found) > 1 else []
        summaries = await asyncio.gather(*(get(SUMMARY + quote(t.replace(" ", "_"), safe=""), None) for t in titles[:3]))
        for s in summaries:
            hit = thumbnail(s, q)
            if hit:
                return hit
    return None
