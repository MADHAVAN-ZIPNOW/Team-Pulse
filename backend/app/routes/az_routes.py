"""Per-person Azure CLI sign-in. Claude can then run `az` commands as that person (within the AZ_POLICY guard-rails)."""
from __future__ import annotations

import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth.dependencies import get_current_session, require_same_origin
from ..config import Settings, get_settings
from ..services import azure_cli
from ..services.az_policy import describe
from ..services.rate_limit import RateLimiter, rate_limit_dependency
from ..services.session_store import Session, cleanup_az

router = APIRouter(prefix="/api/az", tags=["azure-cli"])
_login_limiter = RateLimiter(max_requests=5, window_seconds=60)


class AzStatus(BaseModel):
    enabled: bool
    policy: str
    policyText: str
    signedIn: bool
    user: str | None = None
    managedByLogin: bool = False  # True when the app sign-in already IS the az login (nothing extra to do)


def _require_enabled(settings: Settings) -> None:
    if not settings.az_enabled:
        raise HTTPException(status_code=404, detail="Azure CLI access is not enabled on this server.")


@router.get("/status", response_model=AzStatus)
async def status(
    session: Annotated[Session, Depends(get_current_session)], settings: Annotated[Settings, Depends(get_settings)]
) -> AzStatus:
    base = dict(
        enabled=settings.az_enabled, policy=settings.az_policy, policyText=describe(settings.az_policy),
        managedByLogin=settings.auth_mode == "az_login",
    )
    if not settings.az_enabled or not session.az_dir:
        return AzStatus(**base, signedIn=False)
    try:
        info = await azure_cli.current_user(session.az_dir)
    except azure_cli.AzureCliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    session.az_user = info["email"] if info else None
    return AzStatus(**base, signedIn=info is not None, user=session.az_user)


@router.post(
    "/login",
    dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_login_limiter, "az-login"))],
)
async def login(
    session: Annotated[Session, Depends(get_current_session)], settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, str]:
    """Start `az login --use-device-code` in this person's private config folder; returns the code to enter."""
    _require_enabled(settings)
    if not session.az_dir:
        session.az_dir = tempfile.mkdtemp(prefix="az-user-")
    try:
        return await azure_cli.start_device_login(session.az_dir)
    except azure_cli.AzureCliError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/login", status_code=204, dependencies=[Depends(require_same_origin)])
async def logout(session: Annotated[Session, Depends(get_current_session)]) -> Response:
    cleanup_az(session)
    return Response(status_code=204)
