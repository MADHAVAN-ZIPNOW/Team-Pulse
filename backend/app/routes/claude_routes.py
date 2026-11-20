"""Per-user Claude account: each person can supply their own `claude setup-token` token.

The token lives only in this person's server-side session (memory). It is never returned by any endpoint,
never logged, and is dropped on logout, session expiry or server restart.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ..auth.dependencies import get_current_session, require_same_origin
from ..config import Settings, get_settings
from ..schemas.claude import ClaudeStatus, ClaudeTokenRequest
from ..services.rate_limit import RateLimiter, rate_limit_dependency
from ..services.session_store import Session

router = APIRouter(prefix="/api/claude", tags=["claude-account"])

_token_limiter = RateLimiter(max_requests=10, window_seconds=60)


@router.get("/status", response_model=ClaudeStatus)
async def status(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClaudeStatus:
    return ClaudeStatus(required=settings.require_user_claude_token, hasToken=bool(session.claude_token))


@router.put(
    "/token",
    status_code=204,
    dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_token_limiter, "claude-token"))],
)
async def save_token(body: ClaudeTokenRequest, session: Annotated[Session, Depends(get_current_session)]) -> Response:
    session.claude_token = body.token
    return Response(status_code=204)


@router.delete("/token", status_code=204, dependencies=[Depends(require_same_origin)])
async def remove_token(session: Annotated[Session, Depends(get_current_session)]) -> Response:
    session.claude_token = None
    return Response(status_code=204)
