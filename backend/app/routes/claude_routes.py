"""Per-user Claude account: each person can supply their own `claude setup-token` token.

The token lives only in this person's server-side session (memory). It is never returned by any endpoint,
never logged, and is dropped on logout or when the session has been idle for 30 days (it is saved encrypted so a restart keeps it).
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth.dependencies import get_current_session, require_same_origin
from ..config import Settings, get_settings
from ..schemas.claude import ClaudeSourceRequest, ClaudeStatus, ClaudeTokenRequest
from ..services.rate_limit import RateLimiter, rate_limit_dependency
from ..services.session_store import Session

router = APIRouter(prefix="/api/claude", tags=["claude-account"])

_token_limiter = RateLimiter(max_requests=10, window_seconds=60)


@router.get("/status", response_model=ClaudeStatus)
async def status(
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClaudeStatus:
    pool = [label for label, _ in settings.shared_claude_tokens]
    source = session.claude_source if session.claude_source in pool else "own"
    return ClaudeStatus(
        required=settings.require_user_claude_token, hasToken=bool(session.claude_token), source=source, pool=pool
    )


@router.put("/source", status_code=204, dependencies=[Depends(require_same_origin)])
async def choose_source(
    request: Request,
    body: ClaudeSourceRequest,
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Switch between your own token and a shared account (e.g. when your own limit is reached)."""
    if body.source != "own" and body.source not in {label for label, _ in settings.shared_claude_tokens}:
        raise HTTPException(status_code=422, detail="Unknown Claude account.")
    session.claude_source = body.source
    request.app.state.sessions.save(session)
    return Response(status_code=204)


@router.put(
    "/token",
    status_code=204,
    dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_token_limiter, "claude-token"))],
)
async def save_token(request: Request, body: ClaudeTokenRequest, session: Annotated[Session, Depends(get_current_session)]) -> Response:
    session.claude_token = body.token
    request.app.state.sessions.save(session)
    return Response(status_code=204)


@router.delete("/token", status_code=204, dependencies=[Depends(require_same_origin)])
async def remove_token(request: Request, session: Annotated[Session, Depends(get_current_session)]) -> Response:
    session.claude_token = None
    request.app.state.sessions.save(session)
    return Response(status_code=204)


def effective_claude_token(session: Session, settings: Settings) -> str | None:
    """The token to use for this person's next question: a chosen shared account, else their own."""
    if session.claude_source != "own":
        for label, token in settings.shared_claude_tokens:
            if label == session.claude_source:
                return token
    return session.claude_token


class ModelInfo(BaseModel):
    id: str
    label: str
    note: str


class ModelList(BaseModel):
    models: list[ModelInfo]
    default: str


@router.get("/models", response_model=ModelList)
async def list_models(
    session: Annotated[Session, Depends(get_current_session)], settings: Annotated[Settings, Depends(get_settings)]
) -> ModelList:
    """The Claude models a person can pick for their questions (set by CLAUDE_MODELS)."""
    return ModelList(
        models=[ModelInfo(id=i, label=label, note=note) for i, label, note in settings.claude_models],
        default=settings.claude_models[0][0],
    )
