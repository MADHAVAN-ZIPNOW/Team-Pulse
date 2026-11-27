"""Reads the live Google Trends "Trending now" page the way a browser does. Nothing is stored.

The page ships its whole list (name, search volume, % increase, start and end time, related searches, Google's
category number) inside the HTML for the chosen window (past 4 h / 24 h / 48 h / 7 days). Every trend comes from
Google at the moment of the request.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

ISO = "%Y-%m-%dT%H:%M:%SZ"
WINDOWS = (4, 24, 48, 168)  # the time windows Google's own page offers (hours)
PAGE_URL = "https://trends.google.com/trending?geo={geo}&hours={hours}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_DATA_RE = re.compile(r"AF_initDataCallback\(\{key: 'ds:\d+', hash: '[^']*', data:(.*?), sideChannel", re.S)


class SourceError(Exception):
    """The Google Trends page could not be read (network problem or the page layout changed)."""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


def trend_key(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def traffic_number(text: str) -> int:
    """'200K+' -> 200000, '2M+' -> 2000000, '1,500' -> 1500, '1,000%' -> 1000."""
    m = re.search(r"(\d[\d,]*\.?\d*)\s*([kKmMbB]?)", text or "")
    if not m:
        return 0
    value = float(m.group(1).replace(",", ""))
    return int(value * {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2).lower()])


async def fetch_page(geo: str, hours: int = 24) -> str:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True) as http:
            res = await http.get(
                PAGE_URL.format(geo=geo, hours=hours),
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            )
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not reach Google Trends ({type(exc).__name__}).") from exc
    if res.status_code != 200:
        raise SourceError(f"Google Trends answered HTTP {res.status_code}.")
    return res.text


def _iso(unix: Any) -> str | None:
    if isinstance(unix, (int, float)) and 1_000_000_000 < unix < 4_000_000_000:
        return datetime.fromtimestamp(unix, timezone.utc).strftime(ISO)
    return None


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else None


def format_volume(n: float) -> str:
    n = int(n)
    if n >= 1_000_000:
        return f"{n // 1_000_000}M+"
    if n >= 1_000:
        return f"{n // 1_000}K+"
    return f"{n}+" if n > 0 else ""


def _looks_like_row(x: Any) -> bool:
    return isinstance(x, list) and len(x) >= 10 and isinstance(x[0], str) and isinstance(x[2], str) and isinstance(x[3], list)


def _find_rows(node: Any, depth: int = 0) -> list[list[Any]] | None:
    """The trend list is a long list of rows; find it by shape so a small layout shift does not break us."""
    if depth > 6 or not isinstance(node, list):
        return None
    if node and all(_looks_like_row(x) for x in node[:3]):
        return node
    for child in node:
        found = _find_rows(child, depth + 1)
        if found:
            return found
    return None


def parse_page(html: str, geo: str, now: str | None = None) -> list[dict[str, Any]]:
    """Every trend on the page, in Google's relevance order (`pos`)."""
    rows: list[list[Any]] | None = None
    for m in _DATA_RE.finditer(html):
        try:
            rows = _find_rows(json.loads(m.group(1)))
        except ValueError:
            continue
        if rows:
            break
    if not rows:
        raise SourceError("The Google Trends page has no trend list (its layout may have changed).")
    now = now or utcnow()
    items: list[dict[str, Any]] = []
    for pos, row in enumerate(rows[:1500]):
        title = row[0].strip()
        if not title:
            continue
        end = _iso(_first(row[4])) if len(row) > 4 else None
        volume = int(row[6]) if isinstance(row[6], (int, float)) else 0
        pct = int(row[8]) if len(row) > 8 and isinstance(row[8], (int, float)) else 0
        related = [q for q in (row[9] if isinstance(row[9], list) else []) if isinstance(q, str)]
        cat = _first(row[10]) if len(row) > 10 else None
        items.append({
            "geo": geo,
            "pos": pos,
            "title": title[:200],
            "key": trend_key(title),
            "categoryId": cat if isinstance(cat, int) else 0,
            "category": "Other",
            "volume": volume,
            "traffic": format_volume(volume),
            "increase": f"{pct:,}%" if pct else "",
            "increaseValue": pct,
            "related": related[:8],
            "news": [  # related searches, each a link to that Google search
                {"title": q[:120], "url": "https://www.google.com/search?q=" + quote(q)}
                for q in related[1:6] if q.strip().casefold() != title.casefold()
            ],
            "startedAt": _iso(_first(row[3])) or now,
            "endedAt": end if end and end <= now else None,
        })
    return items
