"""FastAPI dependencies: session lookup, CSRF origin check, per-user Azure DevOps client."""
from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import Depends, HTTPException, Request

from ..azure_devops.client import AzureDevOpsClient
from ..config import Settings, get_settings
from ..services.session_store import SESSION_COOKIE, Session, unsign_value
from .microsoft_auth import AuthError, MicrosoftAuth


def get_auth(request: Request) -> MicrosoftAuth:
    return request.app.state.auth


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


def get_current_session(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Session:
    session_id = unsign_value(request.cookies.get(SESSION_COOKIE), settings.session_secret)
    session = request.app.state.sessions.get(session_id) if session_id else None
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return session


def require_same_origin(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """CSRF defence for state-changing requests: Origin must be the frontend or the backend itself."""
    origin = request.headers.get("origin")
    if origin is None:
        return  # non-browser client; browsers always send Origin on cross-site POSTs
    redirect = urlparse(settings.redirect_uri)
    allowed = {settings.frontend_url, f"{redirect.scheme}://{redirect.netloc}"}
    if origin.rstrip("/") not in allowed:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected.")


async def get_ado_client(
    session: Annotated[Session, Depends(get_current_session)],
    auth: Annotated[MicrosoftAuth, Depends(get_auth)],
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AzureDevOpsClient:
    async def token_provider() -> str:
        try:
            return await auth.get_access_token(session)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return AzureDevOpsClient(http, token_provider, api_version=settings.azure_devops_api_version)
