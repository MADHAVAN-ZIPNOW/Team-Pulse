"""Per-user Claude account: each person can supply their own `claude setup-token` token.

The token lives only in this person's server-side session (memory). It is never returned by any endpoint,
never logged, and is dropped on logout or when the session has been idle for 30 days (it is saved encrypted so a restart keeps it).
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth.dependencies import get_current_session, require_same_origin
from ..config import Settings, get_settings
from ..schemas.claude import ClaudeActiveRequest, ClaudeFeatureModelRequest, ClaudeSourceRequest, ClaudeStatus, ClaudeTokenRequest
from ..services.claude_usage import FEATURES
from ..services.rate_limit import RateLimiter, rate_limit_dependency
from ..services.session_store import Session

router = APIRouter(prefix="/api/claude", tags=["claude-account"])

_token_limiter = RateLimiter(max_requests=10, window_seconds=60)


@router.get("/status", response_model=ClaudeStatus)
async def status(
    request: Request,
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClaudeStatus:
    pool = [label for label, _ in settings.shared_claude_tokens]
    source = session.claude_source if session.claude_source in pool else "own"
    active = _active(request)
    return ClaudeStatus(
        # when an admin has picked one account for the whole app, nobody is asked for their own token
        required=settings.require_user_claude_token and active is None, hasToken=bool(session.claude_token),
        source=active or source, pool=pool, activeAccount=active, isAdmin=is_admin(session, settings),
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


def _active(request: Request) -> str | None:
    usage = getattr(request.app.state, "claude_usage", None)
    return usage.active if usage else None


def is_admin(session: Session, settings: Settings) -> bool:
    who = {(session.user.email or "").lower(), (session.user.name or "").lower()}
    return bool(who & set(settings.admin_users))


def account_label(session: Session, settings: Settings, active: str | None) -> str | None:
    """Which shared account answers this person's next question (None = their own token)."""
    pool = {label for label, _ in settings.shared_claude_tokens}
    if active in pool:
        return active
    return session.claude_source if session.claude_source in pool else None


def effective_claude_token(session: Session, settings: Settings, active: str | None = None) -> str | None:
    """The token to use for this person's next question: the admin's app-wide account, else a chosen shared account, else their own."""
    if active:
        for label, token in settings.shared_claude_tokens:
            if label == active:
                return token
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


@router.get("/usage")
async def usage(request: Request, session: Annotated[Session, Depends(get_current_session)], settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    """Per-person usage through this app (admins only). Names and numbers only; tokens never leave the server."""
    if not is_admin(session, settings):
        raise HTTPException(status_code=403, detail="Only an admin can see Claude usage.")
    u = request.app.state.claude_usage
    cache = request.app.state.claude_limits
    tokens = dict(settings.shared_claude_tokens)
    accounts = [a for a in u.summary("30d", None)["accounts"] if a["name"] in tokens]
    limits = await asyncio.gather(*(cache.get(request.app.state.http, a["name"], tokens[a["name"]]) for a in accounts))
    for a, lim in zip(accounts, limits):
        a["limits"] = lim
    return {
        "active": u.active, "isAdmin": is_admin(session, settings), "accounts": accounts,
        "autoSwitch": u.auto_switch, "switchPercent": request.app.state.switcher.percent, "switchLog": u.switch_log[-5:],
        "mailTo": request.app.state.switcher.notify_to if request.app.state.switcher.mailer else [],
        "features": list(FEATURES), "featureModels": dict(u.models),
        "defaultModels": {
            "PR Assistant": settings.claude_models[0][0], "Problem Radar": settings.radar_model,
            "Google Trends": settings.trends_model, "Meetings": settings.meeting_agent_model,
        },
        "models": [{"id": i, "label": label} for i, label, _ in settings.claude_models],
    }


@router.get("/dashboard")
async def dashboard(
    request: Request,
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    range: str = "24h",
    account: str = "all",
) -> dict:
    """Live numbers for the Claude Monitor page (admins only): real calls recorded by this app + each plan's real 5-hour/weekly limits."""
    if not is_admin(session, settings):
        raise HTTPException(status_code=403, detail="Only an admin can see Claude usage.")
    u = request.app.state.claude_usage
    tokens = dict(settings.shared_claude_tokens)
    data = u.summary(range, None if account == "all" else account)
    pool = [a for a in data["accounts"] if a["name"] in tokens]
    limits = await asyncio.gather(*(request.app.state.claude_limits.get(request.app.state.http, a["name"], tokens[a["name"]]) for a in pool))
    for a, lim in zip(pool, limits):
        a["limits"] = lim
        w5 = ((lim or {}).get("fiveHour") or {}).get("usedPercent", 0)
        w7 = ((lim or {}).get("sevenDay") or {}).get("usedPercent", 0)
        w, err = max(w5, w7), a["errorRate"]
        a["status"] = "Critical" if (w >= 90 or err > 5 or a["rateLimited15m"] >= 3) else "Warning" if (w >= 70 or err > 2 or a["rateLimited15m"] > 0) else "Healthy"
        a["health"] = max(5, round(100 - w * 0.6 - min(30, err * 3) - min(20, a["rateLimited15m"] * 5)))
    data["accounts"] = pool
    data["active"] = u.active
    data["autoSwitch"] = u.auto_switch
    data["switchPercent"] = request.app.state.switcher.percent
    data["switchLog"] = u.switch_log[-5:]
    data["mailTo"] = request.app.state.switcher.notify_to if request.app.state.switcher.mailer else []
    data["featureModels"] = dict(u.models)
    data["defaultModels"] = {
        "PR Assistant": settings.claude_models[0][0], "Problem Radar": settings.radar_model,
        "Google Trends": settings.trends_model, "Meetings": settings.meeting_agent_model,
    }
    data["models"] = [{"id": i, "label": label} for i, label, _ in settings.claude_models]
    return data


@router.put("/active", status_code=204, dependencies=[Depends(require_same_origin)])
async def set_active(
    request: Request,
    body: ClaudeActiveRequest,
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Admin only: make one shared account the one the whole app uses (null = let each person choose)."""
    if not is_admin(session, settings):
        raise HTTPException(status_code=403, detail="Only an admin can change the app-wide Claude account.")
    if body.account and body.account not in {label for label, _ in settings.shared_claude_tokens}:
        raise HTTPException(status_code=422, detail="Unknown Claude account.")
    usage = request.app.state.claude_usage
    before = usage.active
    usage.set_active(body.account or None)
    await request.app.state.switcher.chosen_by_admin(before, usage.active, session.user.name or session.user.email or "an admin")
    return Response(status_code=204)


class AutoBody(BaseModel):
    enabled: bool


@router.put("/auto", status_code=204, dependencies=[Depends(require_same_origin)])
async def set_auto(
    request: Request, body: AutoBody, session: Annotated[Session, Depends(get_current_session)], settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    """Admin only: turn automatic switching (at the limit) on or off."""
    if not is_admin(session, settings):
        raise HTTPException(status_code=403, detail="Only an admin can change this.")
    request.app.state.claude_usage.set_auto(body.enabled)
    if body.enabled:
        await request.app.state.switcher.check(force=True)
    return Response(status_code=204)


@router.put("/feature-model", status_code=204, dependencies=[Depends(require_same_origin)])
async def set_feature_model(
    request: Request,
    body: ClaudeFeatureModelRequest,
    session: Annotated[Session, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Admin only: choose which Claude model one feature uses (null = that feature's own default)."""
    if not is_admin(session, settings):
        raise HTTPException(status_code=403, detail="Only an admin can change models.")
    if body.feature not in FEATURES:
        raise HTTPException(status_code=422, detail="Unknown feature.")
    if body.model and body.model not in {i for i, _, _ in settings.claude_models}:
        raise HTTPException(status_code=422, detail="That Claude model is not available.")
    request.app.state.claude_usage.set_model(body.feature, body.model)
    return Response(status_code=204)
