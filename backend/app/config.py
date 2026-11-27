"""Application settings, loaded from environment variables / a root-level .env file."""
from __future__ import annotations

import logging
import os
import re
import secrets
import tempfile
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
    claude_models: tuple[tuple[str, str, str], ...] = ()  # (id, label, note); the first one is the default
    session_ttl_seconds: int = 365 * 24 * 60 * 60  # idle time before a person must sign in again (they stay signed in until they log out)
    session_dir: str = ""  # where signed-in sessions are saved (encrypted)
    trends_enabled: bool = True
    radar_enabled: bool = True  # the daily Tech Problem Radar tab
    radar_model: str = "sonnet"
    radar_hours: tuple[int, int, int] = (8, 14, 20)  # morning, afternoon and night runs (hours in radar_tz)
    radar_mail_from: str = ""  # the mailbox the radar email is sent from (needs the Mail.Send app permission)
    radar_mail_to: tuple[str, ...] = ()
    radar_page_url: str = ""
    radar_tz: str = "Asia/Kolkata"
    radar_dir: str = ""
    radar_watchlist: str = "FastAPI, React, Vite, TypeScript, Python, Docker, Render, Azure DevOps, Microsoft Graph API, Microsoft Entra ID (MSAL), Claude API and Claude Code"
    radar_domains: str = ""  # comma-separated domains the radar covers and the search box offers ("" = the built-in broad list)
    radar_languages: str = "Python, TypeScript"
    radar_platforms: str = "Azure, Render, Docker"
    radar_role: str = "Software engineer"
    radar_skill: str = "Intermediate"
    radar_stack: str = "FastAPI backend, React + Vite frontend, one Docker container on Render, Azure DevOps, Microsoft Entra sign-in, Claude Agent SDK"
    trends_cache_seconds: int = 180  # a Google list is kept in memory this long (never written to disk)
    trends_regions: tuple[str, ...] = ("IN", "US", "GB", "CA", "AU", "DE", "FR", "JP", "BR", "SG")  # merged for "Worldwide"
    trends_model: str = "haiku"  # quick jobs (naming categories)
    trends_research_model: str = "sonnet"  # web research (a trend's photo and analysis, old dates)
    meeting_agent_enabled: bool = False  # lets Claude create Azure DevOps work items from what is said in a meeting
    meeting_agent_model: str = "sonnet"
    meeting_dir: str = ""  # where saved meetings live (encrypted, one private folder per person)
    meeting_retention_days: int = 90
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    speech_locales: tuple[str, ...] = ("en-IN", "ta-IN")
    ffmpeg_path: str = ""
    whisper_model: str = "medium"  # free local speech-to-text for recordings ("" turns it off; "medium" is better for Tamil but slower)
    meeting_upload_max_mb: int = 600

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


_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,60}$")
_MODEL_NOTES = {
    "haiku": "Fastest, for quick questions",
    "sonnet": "Balanced: good for most reviews",
    "opus": "Deepest thinking, for big or risky PRs",
}


def _parse_models(raw: str) -> tuple[tuple[str, str, str], ...]:
    """CLAUDE_MODELS="sonnet=Claude Sonnet;opus=Claude Opus" -> (("sonnet", "Claude Sonnet", note), ...). First is the default.

    Use the aliases sonnet/opus/haiku (always the newest the installed Claude Code knows) or a full model id."""
    items: list[tuple[str, str, str]] = []
    for entry in re.split(r"[;\n]+", raw or "sonnet=Claude Sonnet;opus=Claude Opus;haiku=Claude Haiku"):
        model_id, _, label = entry.strip().partition("=")
        model_id, label = model_id.strip(), label.strip()
        if _MODEL_ID_RE.match(model_id):
            items.append((model_id, label or model_id, _MODEL_NOTES.get(model_id, "")))
        elif entry.strip():
            logger.warning("Ignoring a malformed CLAUDE_MODELS entry")
    return tuple(items) or (("sonnet", "Claude Sonnet", _MODEL_NOTES["sonnet"]),)


def _parse_regions(raw: str) -> tuple[str, ...]:
    """TRENDS_REGIONS="IN,US,GB" -> ("IN", "US", "GB"). Only two-letter region codes are accepted."""
    codes = [c.strip().upper() for c in re.split(r"[,;\s]+", raw or "") if c.strip()]
    valid = tuple(dict.fromkeys(c for c in codes if re.fullmatch(r"[A-Z]{2}", c)))
    return valid or ("IN", "US", "GB", "CA", "AU", "DE", "FR", "JP", "BR", "SG")


def _dir(name: str, default: str) -> str:
    """A folder setting. Git Bash on Windows rewrites a Linux path such as /home/app/data into C:/Program Files/Git/home/app/data
    when it is passed to `docker run -e`, which silently moves the data out of its volume. Undo that rewrite."""
    raw = os.getenv(name, "").strip()
    fixed = re.sub(r"^[A-Za-z]:[/\\]Program Files[/\\]Git(?=[/\\])", "", raw).replace("\\", "/") if raw.startswith(("C:", "c:")) else raw
    return fixed or default


def _hours(raw: str) -> tuple[int, int, int]:
    """"8,14,20" -> the morning, afternoon and night hours (must rise through the day)."""
    try:
        h = tuple(int(x) for x in raw.split(",") if x.strip() != "")
    except ValueError:
        h = ()
    return h if len(h) == 3 and all(0 <= x <= 23 for x in h) and h[0] < h[1] < h[2] else (8, 14, 20)  # type: ignore[return-value]



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
        session_dir=_dir("SESSION_DIR", str(Path(tempfile.gettempdir()) / "pra-sessions")),
        trends_enabled=_bool("TRENDS_ENABLED", True),
        radar_enabled=_bool("RADAR_ENABLED", True),
        radar_model=os.getenv("RADAR_MODEL", "sonnet").strip() or "sonnet",
        radar_hours=_hours(os.getenv("RADAR_HOURS", "")),
        radar_mail_from=os.getenv("RADAR_MAIL_FROM", "").strip(),
        radar_mail_to=tuple(a.strip() for a in os.getenv("RADAR_MAIL_TO", "").split(",") if re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", a.strip())),
        radar_page_url=os.getenv("RADAR_PAGE_URL", "").strip(),
        radar_tz=os.getenv("RADAR_TZ", "Asia/Kolkata").strip() or "Asia/Kolkata",
        radar_dir=_dir("RADAR_DIR", str(PROJECT_ROOT / "data" / "radar")),
        radar_watchlist=os.getenv("RADAR_WATCHLIST", "").strip() or Settings.radar_watchlist,
        radar_domains=os.getenv("RADAR_DOMAINS", "").strip(),
        radar_languages=os.getenv("RADAR_LANGUAGES", "").strip() or Settings.radar_languages,
        radar_platforms=os.getenv("RADAR_PLATFORMS", "").strip() or Settings.radar_platforms,
        radar_role=os.getenv("RADAR_ROLE", "").strip() or Settings.radar_role,
        radar_skill=os.getenv("RADAR_SKILL", "").strip() or Settings.radar_skill,
        radar_stack=os.getenv("RADAR_STACK", "").strip() or Settings.radar_stack,
        trends_cache_seconds=max(30, int(os.getenv("TRENDS_CACHE_SECONDS", "180") or 180)),
        trends_research_model=os.getenv("TRENDS_RESEARCH_MODEL", "sonnet").strip() or "sonnet",
        meeting_agent_enabled=_bool("MEETING_AGENT_ENABLED", False),
        meeting_agent_model=os.getenv("MEETING_AGENT_MODEL", "sonnet").strip() or "sonnet",
        meeting_dir=_dir("MEETING_DIR", str(PROJECT_ROOT / "data" / "meetings")),
        meeting_retention_days=max(1, int(os.getenv("MEETING_RETENTION_DAYS", "90") or 90)),
        azure_speech_key=os.getenv("AZURE_SPEECH_KEY", "").strip(),
        azure_speech_region=re.sub(r"[^a-z0-9-]", "", os.getenv("AZURE_SPEECH_REGION", "").strip().lower()),
        speech_locales=tuple(c.strip() for c in os.getenv("SPEECH_LOCALES", "en-IN,ta-IN").split(",") if re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", c.strip())) or ("en-IN", "ta-IN"),
        ffmpeg_path=os.getenv("FFMPEG_PATH", "").strip(),
        whisper_model="" if os.getenv("WHISPER_MODEL", "").strip().lower() in ("off", "none", "no") else re.sub(r"[^a-z0-9._-]", "", os.getenv("WHISPER_MODEL", "medium").strip().lower()),
        meeting_upload_max_mb=max(1, int(os.getenv("MEETING_UPLOAD_MAX_MB", "600") or 600)),
        trends_regions=_parse_regions(os.getenv("TRENDS_REGIONS", "")),
        trends_model=os.getenv("TRENDS_MODEL", "haiku").strip() or "haiku",
        session_ttl_seconds=int(os.getenv("SESSION_TTL_DAYS", "365") or 365) * 24 * 3600,
        azure_devops_api_version=os.getenv("AZURE_DEVOPS_API_VERSION", "7.1").strip() or "7.1",
        local_repo_path=os.getenv("LOCAL_REPO_PATH", "").strip(),
        backend_port=int(os.getenv("BACKEND_PORT", "8000") or "8000"),
        # Browsers treat http://localhost as a secure context, so Secure cookies work locally.
        cookie_secure=_bool("COOKIE_SECURE", True),
        shared_claude_tokens=_parse_shared_tokens(os.getenv("SHARED_CLAUDE_TOKENS", "")),
        claude_models=_parse_models(os.getenv("CLAUDE_MODELS", "")),
        az_enabled=_bool("AZ_ENABLED", False) or _auth_mode(os.getenv("AUTH_MODE", "")) == "az_login",
        az_policy=_az_policy(os.getenv("AZ_POLICY", "read")),
        require_user_claude_token=_bool("REQUIRE_USER_CLAUDE_TOKEN", False),
        allowed_users=tuple(u.strip().lower() for u in os.getenv("ALLOWED_USERS", "").split(",") if u.strip()),
        auth_mode=_auth_mode(os.getenv("AUTH_MODE", "")),
    )
