"""Google Trends for the Trends page. Everything is read live from Google (or researched by Claude) and kept only in
memory: no trend is ever stored in a database or file.

geo is a two-letter country code, or WW for "worldwide" = the configured countries merged (a trend shows once, with its countries).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_session, require_same_origin
from ..config import Settings, get_settings
from ..services.countries import COUNTRY_CODES
from ..services.rate_limit import RateLimiter, rate_limit_dependency
from ..services.trends_service import TrendsService, public, select, stats as compute_stats
from ..services.trends_source import ISO, SourceError, WINDOWS, utcnow
from ..claude.claude_code_service import ClaudeCodeError

router = APIRouter(prefix="/api/trends", tags=["trends"], dependencies=[Depends(get_current_session)])

_write_limiter = RateLimiter(max_requests=30, window_seconds=60)
_research_limiter = RateLimiter(max_requests=60, window_seconds=60)
_chat_limiter = RateLimiter(max_requests=12, window_seconds=60)
_curve_limiter = RateLimiter(max_requests=240, window_seconds=60)
WORLD = "WW"
MAX_PAST_DAYS = 366


def _service(request: Request) -> TrendsService:
    svc = getattr(request.app.state, "trends", None)
    if svc is None:
        raise HTTPException(status_code=404, detail="Google Trends is turned off on this server (TRENDS_ENABLED).")
    return svc


def _geo(raw: str) -> str:
    geo = raw.upper()
    if geo != WORLD and geo not in COUNTRY_CODES:
        raise HTTPException(status_code=404, detail="Unknown country.")
    return geo


def _hours(hours: int) -> int:
    if hours not in WINDOWS:
        raise HTTPException(status_code=422, detail=f"Invalid request: hours must be one of {', '.join(map(str, WINDOWS))}.")
    return hours


async def _listing(svc: TrendsService, geo: str, hours: int, force: bool = False):  # type: ignore[no-untyped-def]
    try:
        return await svc.listing(geo, hours, force)
    except (SourceError, ClaudeCodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Could not read Google Trends right now: {exc}") from exc


@router.get("/meta")
async def meta(request: Request) -> dict[str, Any]:
    svc = _service(request)
    return {
        "countries": list(COUNTRY_CODES),
        "world": list(svc.world_regions),
        "windows": list(WINDOWS),
        "cacheSeconds": svc.cache_seconds,
        "now": utcnow(),
    }


@router.get("")
async def list_trends(
    request: Request,
    geo: str = Query(min_length=2, max_length=2),
    hours: int = 24,
    status: Literal["active", "ended", "all"] = "active",
    category: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=100),
    sort: Literal["rank", "newest", "oldest", "longest", "traffic"] = "rank",
    limit: int = Query(default=50, ge=1, le=1500),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> dict[str, Any]:
    svc = _service(request)
    items, fetched_at, stale = await _listing(svc, _geo(geo), _hours(hours))
    page, total = select(items, status=status, category=category or None, text=(q or "").strip() or None, sort=sort, offset=offset, limit=limit)
    return {
        "items": [public(i) for i in page],
        "total": total,
        "fetchedAt": fetched_at,
        "stale": stale,
        "categories": sorted({i["category"] for i in items}),
    }


@router.get("/stats")
async def stats(request: Request, geo: str = Query(min_length=2, max_length=2), hours: int = 24) -> dict[str, Any]:
    svc = _service(request)
    items, _, _ = await _listing(svc, _geo(geo), _hours(hours))
    return compute_stats(items)


@router.post("/refresh", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "trends-refresh"))])
async def refresh(request: Request, geo: str = Query(min_length=2, max_length=2), hours: int = 24) -> dict[str, Any]:
    """Read Google again right now, ignoring the short in-memory copy."""
    svc = _service(request)
    _, fetched_at, stale = await _listing(svc, _geo(geo), _hours(hours), force=True)
    return {"fetchedAt": fetched_at, "stale": stale}


@router.get("/series", dependencies=[Depends(rate_limit_dependency(_curve_limiter, "trends-series"))])
async def series(
    request: Request, geo: str = Query(min_length=2, max_length=2), hours: int = 24, title: str = Query(min_length=1, max_length=200)
) -> dict[str, Any]:
    """The real up-and-down interest curve of one trend over the window (Google's 0-100 scale), or values = null."""
    svc = _service(request)
    data = await svc.curves.series(title, _geo(geo), _hours(hours))
    return data or {"t0": None, "t1": None, "values": None}


@router.get("/photo", dependencies=[Depends(rate_limit_dependency(_research_limiter, "trends-photo"))])
async def photo(request: Request, title: str = Query(min_length=1, max_length=200)) -> dict[str, Any]:
    """A picture for a trend: Wikipedia first (about half a second); if it has none, Claude searches the web in the background
    (pending = true: ask again in a few seconds)."""
    return await _service(request).photo(title)


@router.post("/past", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_research_limiter, "trends-past"))])
async def past(
    request: Request,
    geo: str = Query(min_length=2, max_length=2),
    start: str = Query(min_length=10, max_length=10, alias="from"),
    to: str = Query(min_length=10, max_length=10),
) -> dict[str, Any]:
    """Old dates: Claude goes to Google Trends / Year in Search / news and reads what was trending. Slow (up to a few minutes
    the first time), so call again until status is "ready"."""
    svc = _service(request)
    geo = _geo(geo)
    try:
        d1, d2 = date.fromisoformat(start), date.fromisoformat(to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid request: from/to must be dates like 2024-03-05.") from exc
    today = date.today() + timedelta(days=1)
    if d1 > d2 or d2 > today or d1 < date(2004, 1, 1) or (d2 - d1).days > MAX_PAST_DAYS:
        raise HTTPException(status_code=422, detail="Invalid request: pick a past date range of at most one year.")
    return await svc.past(geo, d1.isoformat(), d2.isoformat())


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=6000)


class ChatBody(BaseModel):
    geo: str = Field(min_length=2, max_length=2)
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=1500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    deep: bool = False


@router.post("/chat", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_chat_limiter, "trends-chat"))])
async def chat(request: Request, body: ChatBody) -> StreamingResponse:
    """Ask Claude about one trend. Streams server-sent events: status (what it is doing), delta (answer text), error, done."""
    svc = _service(request)
    geo = _geo(body.geo)
    history = [t.model_dump() for t in body.history]

    async def events():  # type: ignore[no-untyped-def]
        async for ev in svc.chat(geo, body.title, body.question.strip(), history, body.deep):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
