"""FastAPI application entry point. Local use only."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth.dependencies import get_current_session
from .auth.microsoft_auth import MicrosoftAuth
from .azure_devops.client import AzureDevOpsError
from .claude.claude_code_service import ClaudeCodeService
from .config import Settings, get_settings
from .routes import auth_routes, az_routes, azure_routes, chat_routes, claude_routes, meeting_routes, radar_routes, trends_routes
from .services.claude_switcher import AccountSwitcher
from .services.claude_usage import ClaudeUsage, LimitsCache
from .services.rate_limit import RateLimiter
from .services.session_store import PendingFlowStore, SessionStore
from .services.meeting_agent import MeetingAgent
from .services.meeting_auto import AutoProcessor
from .services.meeting_store import MeetingStore
from .services import speech_local
from .services.radar_mail import Mailer
from .services.radar_service import DEFAULT_DOMAINS, RadarService
from .services.trends_service import TrendsService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pr_assistant")

# Claude Code gets 60 seconds to start by default. On a busy PC that is not enough ("Control request timeout: initialize"), so give it 5 minutes.
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "300000")

_global_limiter = RateLimiter(max_requests=240, window_seconds=60)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if missing := settings.missing_auth_config():
        logger.warning("Missing configuration: %s (sign-in will not work until set in .env)", ", ".join(missing))
    if not settings.local_repo_path:
        logger.warning("LOCAL_REPO_PATH is not set; /api/ask will fail until it is configured.")
    def _msal_app() -> Any:  # an empty MSAL app: a saved Microsoft sign-in is loaded into it after a restart
        import msal

        return msal.ConfidentialClientApplication(
            settings.client_id, client_credential=settings.client_secret, authority=settings.authority, token_cache=msal.SerializableTokenCache(),
        )

    app.state.sessions = SessionStore(
        settings.session_ttl_seconds, settings.session_dir, settings.session_secret, None if settings.missing_auth_config() else _msal_app,
    )
    app.state.flows = PendingFlowStore()
    app.state.auth = MicrosoftAuth(settings, app.state.sessions, app.state.flows)
    app.state.claude = ClaudeCodeService(settings.local_repo_path)
    app.state.claude_limits = LimitsCache()
    app.state.claude_usage = ClaudeUsage([label for label, _ in settings.shared_claude_tokens], settings.default_claude_account, dict(settings.shared_claude_tokens), running=lambda: app.state.claude.inflight)
    app.state.claude.model_override = app.state.claude_usage.models.get
    app.state.az_pending = {}  # pending az code sign-ins: id -> (started, private az folder)
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), limits=httpx.Limits(max_connections=20)
    )
    notify_to = list(dict.fromkeys(a.lower() for a in [*settings.claude_notify_to, *settings.admin_users, *settings.radar_mail_to]))
    claude_mailer = None
    if settings.radar_mail_from and settings.tenant_id and settings.client_id and settings.client_secret:
        claude_mailer = Mailer(settings.tenant_id, settings.client_id, settings.client_secret, settings.radar_mail_from, notify_to, app.state.http)
    app.state.switcher = AccountSwitcher(
        app.state.claude_usage, app.state.claude_limits, app.state.http, dict(settings.shared_claude_tokens), claude_mailer, notify_to,
        settings.claude_switch_percent,
    )
    switch_task = asyncio.create_task(app.state.switcher.run_forever()) if settings.shared_claude_tokens else None

    def _on_claude_event(ev: dict) -> None:
        app.state.claude_usage.record_event(ev)
        err = (ev.get("error") or "").lower()
        if not ev.get("ok") and ("429" in err or "limit" in err) and app.state.claude_usage.active:
            asyncio.get_running_loop().create_task(app.state.switcher.check(force=True))  # a refused call: look at the limits right now

    app.state.claude.on_event = _on_claude_event
    app.state.meetings = None
    app.state.auto = None
    auto_task: asyncio.Task[None] | None = None
    if settings.meeting_agent_enabled:
        store = None
        if os.getenv("SESSION_SECRET", "").strip():  # the saved files are encrypted with it, so it must stay the same between restarts
            store = MeetingStore(settings.meeting_dir, settings.session_secret, settings.meeting_retention_days)
        else:
            logger.warning("SESSION_SECRET is not set: meetings are NOT saved (they could not be read again after a restart).")
        app.state.meetings = MeetingAgent(app.state.claude, settings.meeting_agent_model, store=store)
        if settings.auth_mode == "msal":  # reading Teams needs the Microsoft sign-in
            app.state.auto = AutoProcessor(app.state.meetings, app.state.auth, app.state.sessions, app.state.http, settings)
            auto_task = asyncio.create_task(app.state.auto.run_forever())
    app.state.trends = None
    trends_task: asyncio.Task[None] | None = None
    if settings.trends_enabled:  # nothing is stored: lists are read live from Google and kept in memory only
        app.state.trends = TrendsService(
            app.state.claude, settings.trends_regions, settings.trends_model, settings.trends_cache_seconds,
            settings.trends_research_model,
        )
        trends_task = asyncio.create_task(app.state.trends.run_forever())
    whisper_task: asyncio.Task[None] | None = None
    if settings.meeting_agent_enabled and settings.whisper_model and speech_local.available():
        whisper_task = asyncio.create_task(speech_local.warm(settings.whisper_model))
    app.state.radar = None
    radar_task: asyncio.Task[None] | None = None
    if settings.radar_enabled:  # one research run a day; reports are small public-news JSON files
        mailer = None
        if settings.radar_mail_from and settings.radar_mail_to and settings.tenant_id and settings.client_id and settings.client_secret:
            mailer = Mailer(
                settings.tenant_id, settings.client_id, settings.client_secret, settings.radar_mail_from, list(settings.radar_mail_to),
                app.state.http, settings.radar_page_url,
            )
        app.state.radar = RadarService(
            app.state.claude, settings.radar_model, settings.radar_dir, settings.radar_hours, settings.radar_tz,
            {"watchlist": settings.radar_watchlist, "domains": settings.radar_domains or DEFAULT_DOMAINS, "languages": settings.radar_languages, "platforms": settings.radar_platforms,
             "role": settings.radar_role, "skill": settings.radar_skill, "stack": settings.radar_stack},
            mailer,
        )
        radar_task = asyncio.create_task(app.state.radar.run_forever())
    try:
        yield
    finally:
        if switch_task:
            switch_task.cancel()
        if radar_task:
            radar_task.cancel()
        if whisper_task:
            whisper_task.cancel()
        if app.state.meetings is not None:
            app.state.meetings.flush_now()  # save anything not yet written
        if auto_task:
            auto_task.cancel()
        if trends_task:
            trends_task.cancel()
        if app.state.trends:
            await app.state.trends.curves.aclose()
        await app.state.http.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Azure DevOps PR Assistant", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def global_rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        client = request.client.host if request.client else "unknown"
        if not _global_limiter.check(client):
            return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AzureDevOpsError)
    async def ado_error_handler(_: Request, exc: AzureDevOpsError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        custom = [e["msg"].removeprefix("Value error, ") for e in errors if e["type"] == "value_error"]
        if custom:  # messages we wrote ourselves are safe and more useful than a field list
            return JSONResponse({"detail": f"Invalid request: {custom[0]}."}, status_code=422)
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]) for e in errors})
        return JSONResponse({"detail": f"Invalid request: check {', '.join(fields)}."}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error")
        return JSONResponse({"detail": "Internal server error."}, status_code=500)

    @app.get("/api/health")
    async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
        return {
            "status": "ok",
            "authMode": settings.auth_mode,
            "authConfigured": not settings.missing_auth_config(),
            "repoConfigured": bool(settings.local_repo_path),
        }

    app.include_router(auth_routes.router)
    app.include_router(azure_routes.router, dependencies=[Depends(get_current_session)])
    app.include_router(chat_routes.router)
    app.include_router(claude_routes.router)
    app.include_router(az_routes.router)
    app.include_router(trends_routes.router)
    app.include_router(radar_routes.router)
    app.include_router(meeting_routes.router)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """If a built UI exists (Docker image / `npm run build`), serve it from the same origin as the API."""
    dist = Path(os.getenv("STATIC_DIR") or Path(__file__).resolve().parents[2] / "frontend" / "dist")
    index = dist / "index.html"
    if not index.is_file():
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "auth/")):
            raise HTTPException(status_code=404, detail="Not found.")
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=get_settings().backend_port)
