"""Application settings, loaded from environment variables / a root-level .env file."""
from __future__ import annotations

import logging
import os
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("pr_assistant.config")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# Azure DevOps first-party resource id. "user_impersonation" is the delegated scope.
AZURE_DEVOPS_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"
AZURE_DEVOPS_SCOPE = f"{AZURE_DEVOPS_RESOURCE_ID}/user_impersonation"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    client_secret: str
    redirect_uri: str
    frontend_url: str
    session_secret: str
    azure_devops_api_version: str
    local_repo_path: str
    backend_port: int
    cookie_secure: bool
    auth_mode: str
    allowed_users: tuple[str, ...]
    require_user_claude_token: bool
    az_enabled: bool
    az_policy: str
    shared_claude_tokens: tuple[tuple[str, str], ...]
    session_ttl_seconds: int = 8 * 60 * 60

    # Limits for what is sent to Claude Code
    max_question_chars: int = 2000
    max_changed_files: int = 40
    max_diff_chars_per_file: int = 12_000
    max_diff_chars_total: int = 100_000
    max_comments: int = 40

    def missing_auth_config(self) -> list[str]:
        if self.auth_mode in ("azure_cli", "az_login"):
            return []  # tokens come from `az login`; no app registration needed
        missing = []
        for env_name, value in (
            ("AZURE_TENANT_ID", self.tenant_id),
            ("AZURE_CLIENT_ID", self.client_id),
            ("AZURE_CLIENT_SECRET", self.client_secret),
        ):
            if not value:
                missing.append(env_name)
        return missing

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


def _auth_mode(raw: str) -> str:
    """msal (default, Entra app registration) | azure_cli (one shared az login, local use) | az_login (each person's own az login)."""
    value = (raw or "").strip().lower()
    return value if value in ("azure_cli", "az_login") else "msal"


def _az_policy(raw: str) -> str:
    value = (raw or "read").strip().lower()
    return value if value in ("read", "write", "full") else "read"


_LABEL_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,40}$")
_TOKEN_RE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,400}$")


def _parse_shared_tokens(raw: str) -> tuple[tuple[str, str], ...]:
    """SHARED_CLAUDE_TOKENS="Abinaya=sk-ant-...;Monika=sk-ant-..." -> (("Abinaya", "sk-ant-..."), ...). Bad entries are skipped."""
    pairs: list[tuple[str, str]] = []
    for entry in re.split(r"[;\n]+", raw):
        label, _, token = entry.strip().partition("=")
        label, token = label.strip(), token.strip()
        if _LABEL_RE.match(label) and _TOKEN_RE.match(token) and label.lower() != "own":
            pairs.append((label, token))
        elif entry.strip():
            logger.warning("Ignoring a malformed SHARED_CLAUDE_TOKENS entry")
    return tuple(pairs)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    secret = os.getenv("SESSION_SECRET", "").strip()
    if not secret:
        # Sessions are in-memory anyway, so a per-process secret is acceptable for local use.
        secret = secrets.token_urlsafe(48)
        logger.warning("SESSION_SECRET is not set; using a random per-process secret.")
    return Settings(
        tenant_id=os.getenv("AZURE_TENANT_ID", "").strip(),
        client_id=os.getenv("AZURE_CLIENT_ID", "").strip(),
        client_secret=os.getenv("AZURE_CLIENT_SECRET", "").strip(),
        redirect_uri=os.getenv("AZURE_REDIRECT_URI", "http://localhost:8000/auth/callback").strip(),
        frontend_url=os.getenv("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/"),
        session_secret=secret,
        azure_devops_api_version=os.getenv("AZURE_DEVOPS_API_VERSION", "7.1").strip() or "7.1",
        local_repo_path=os.getenv("LOCAL_REPO_PATH", "").strip(),
        backend_port=int(os.getenv("BACKEND_PORT", "8000") or "8000"),
        # Browsers treat http://localhost as a secure context, so Secure cookies work locally.
        cookie_secure=_bool("COOKIE_SECURE", True),
        shared_claude_tokens=_parse_shared_tokens(os.getenv("SHARED_CLAUDE_TOKENS", "")),
        az_enabled=_bool("AZ_ENABLED", False) or _auth_mode(os.getenv("AUTH_MODE", "")) == "az_login",
        az_policy=_az_policy(os.getenv("AZ_POLICY", "read")),
        require_user_claude_token=_bool("REQUIRE_USER_CLAUDE_TOKEN", False),
        allowed_users=tuple(u.strip().lower() for u in os.getenv("ALLOWED_USERS", "").split(",") if u.strip()),
        auth_mode=_auth_mode(os.getenv("AUTH_MODE", "")),
    )
