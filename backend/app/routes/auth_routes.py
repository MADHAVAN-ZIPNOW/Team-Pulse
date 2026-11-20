from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..auth.dependencies import get_auth, get_current_session, require_same_origin
from ..auth.microsoft_auth import AuthError, MicrosoftAuth
from ..config import Settings, get_settings
from ..services import azure_cli
from ..services.session_store import OAUTH_STATE_COOKIE, SESSION_COOKIE, Session, UserProfile, sign_value, unsign_value

logger = logging.getLogger("pr_assistant.auth_routes")
router = APIRouter(prefix="/auth", tags=["auth"])


class MeResponse(BaseModel):
    name: str
    email: str | None
    tenantId: str | None


def _frontend_redirect(settings: Settings, **params: str) -> RedirectResponse:
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"{settings.frontend_url}/{query}", status_code=302)


@router.get("/login")
async def login(
    auth: Annotated[MicrosoftAuth, Depends(get_auth)], settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    try:
        auth_uri, state = auth.start_login()
    except AuthError as exc:
        return _frontend_redirect(settings, auth_error=exc.message)
    response = RedirectResponse(auth_uri, status_code=302)
    # Binds the OAuth state to this browser (login-CSRF protection).
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        sign_value(state, settings.session_secret),
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/auth",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    auth: Annotated[MicrosoftAuth, Depends(get_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    cookie_state = unsign_value(request.cookies.get(OAUTH_STATE_COOKIE), settings.session_secret)
    try:
        session = auth.complete_login(dict(request.query_params), cookie_state)
    except AuthError as exc:
        response = _frontend_redirect(settings, auth_error=exc.message)
        response.delete_cookie(OAUTH_STATE_COOKIE, path="/auth")
        return response
    response = _frontend_redirect(settings)
    response.delete_cookie(OAUTH_STATE_COOKIE, path="/auth")
    response.set_cookie(
        SESSION_COOKIE,
        sign_value(session.id, settings.session_secret),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout", dependencies=[Depends(require_same_origin)])
async def logout(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    session_id = unsign_value(request.cookies.get(SESSION_COOKIE), settings.session_secret)
    if session_id:
        request.app.state.sessions.delete(session_id)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/cli/start", dependencies=[Depends(require_same_origin)])
async def cli_start(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    """Azure CLI mode: begin `az login --use-device-code` and return the code to enter."""
    if settings.auth_mode != "azure_cli":
        raise HTTPException(status_code=404, detail="Azure CLI sign-in is not enabled (set AUTH_MODE=azure_cli).")
    try:
        return await azure_cli.start_device_login()
    except azure_cli.AzureCliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/cli/status")
async def cli_status(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    """Polled by the UI: once `az` is signed in, create a normal app session."""
    if settings.auth_mode != "azure_cli":
        raise HTTPException(status_code=404, detail="Azure CLI sign-in is not enabled.")
    try:
        info = await azure_cli.current_user()
    except azure_cli.AzureCliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if info is None:
        return JSONResponse({"signedIn": False})
    user = UserProfile(name=info["name"] or "", email=info["email"], tenant_id=info["tenantId"], object_id=None)
    session = request.app.state.sessions.create(None, {}, user)
    response = JSONResponse({"signedIn": True})
    response.set_cookie(
        SESSION_COOKIE,
        sign_value(session.id, settings.session_secret),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/me", response_model=MeResponse)
async def me(session: Annotated[Session, Depends(get_current_session)]) -> MeResponse:
    return MeResponse(name=session.user.name, email=session.user.email, tenantId=session.user.tenant_id)
