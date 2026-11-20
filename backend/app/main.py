"""FastAPI application entry point. Local use only."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

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
from .routes import auth_routes, azure_routes, chat_routes, claude_routes
from .services.rate_limit import RateLimiter
from .services.session_store import PendingFlowStore, SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pr_assistant")

_global_limiter = RateLimiter(max_requests=240, window_seconds=60)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if missing := settings.missing_auth_config():
        logger.warning("Missing configuration: %s (sign-in will not work until set in .env)", ", ".join(missing))
    if not settings.local_repo_path:
        logger.warning("LOCAL_REPO_PATH is not set; /api/ask will fail until it is configured.")
    app.state.sessions = SessionStore(settings.session_ttl_seconds)
    app.state.flows = PendingFlowStore()
    app.state.auth = MicrosoftAuth(settings, app.state.sessions, app.state.flows)
    app.state.claude = ClaudeCodeService(settings.local_repo_path)
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), limits=httpx.Limits(max_connections=20)
    )
    try:
        yield
    finally:
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
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]) for e in exc.errors()})
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
