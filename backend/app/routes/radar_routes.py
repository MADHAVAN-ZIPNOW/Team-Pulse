"""Problem Radar: the saved reports. The radar researches by itself three times a day; nobody has to start it."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_session, require_same_origin
from ..claude.claude_code_service import ClaudeCodeError
from ..services.radar_service import RadarService
from ..services.rate_limit import RateLimiter, rate_limit_dependency

router = APIRouter(prefix="/api/radar", tags=["radar"], dependencies=[Depends(get_current_session)])


_search_limiter = RateLimiter(max_requests=8, window_seconds=60)


class SearchBody(BaseModel):
    query: str = Field(min_length=3, max_length=200)
    domain: str = Field(default="", max_length=80)


def _svc(request: Request) -> RadarService:
    svc = getattr(request.app.state, "radar", None)
    if svc is None:
        raise HTTPException(status_code=404, detail="The Problem Radar is turned off on this server (RADAR_ENABLED).")
    return svc


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    """The newest report and the radar's state (running, next run, which reports exist, email)."""
    svc = _svc(request)
    return {"status": svc.status(), "report": svc.latest()}


@router.post("/search", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_search_limiter, "radar-search"))])
async def start_search(request: Request, body: SearchBody) -> dict[str, str]:
    """Claude researches the typed problem or domain now (a few minutes). Poll GET /search/{id}."""
    svc = _svc(request)
    if body.domain and body.domain not in svc.domains():
        raise HTTPException(status_code=422, detail="Choose a domain from the list.")
    try:
        return {"id": svc.start_search(body.query, body.domain)}
    except ClaudeCodeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/search/{sid}")
async def search_state(request: Request, sid: str) -> dict[str, Any]:
    state = _svc(request).search_state(sid)
    if state is None:
        raise HTTPException(status_code=404, detail="That search is no longer available. Search again.")
    return state


@router.get("/{key}")
async def one_report(request: Request, key: str) -> dict[str, Any]:
    report = _svc(request).get(key)
    if report is None:
        raise HTTPException(status_code=404, detail="There is no report with that name.")
    return report
