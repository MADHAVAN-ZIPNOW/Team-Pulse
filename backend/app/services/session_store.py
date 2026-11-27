"""Server-side session storage. Tokens never go to the browser.

Sessions live in memory and are also written encrypted to SESSION_DIR (Microsoft sign-in included, with its token
cache) so a restart or a new deployment does not sign people out again. A session lasts until log out, or until it
has been idle for the whole TTL (a year by default; Microsoft itself asks for a new sign-in after 90 days of not using the app)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import hmac
import secrets
import shutil
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("pr_assistant.sessions")

SESSION_COOKIE = "pra_session"
OAUTH_STATE_COOKIE = "pra_oauth_state"
FLOW_TTL_SECONDS = 600


@dataclass
class UserProfile:
    name: str
    email: str | None
    tenant_id: str | None
    object_id: str | None


@dataclass
class Session:
    id: str
    msal_app: Any  # msal.ConfidentialClientApplication holding this user's token cache
    account: dict[str, Any]
    user: UserProfile
    claude_token: str | None = field(default=None, repr=False)  # this person's own Claude token
    az_dir: str | None = None  # this person's private Azure CLI config folder (their own az sign-in)
    az_user: str | None = None  # who az is signed in as, once verified
    claude_source: str = "own"  # "own" or the label of a shared account chosen by the owner
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    saved_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def cleanup_az(session: "Session") -> None:
    """Remove the person's az sign-in folder (tokens) from disk."""
    if session.az_dir:
        shutil.rmtree(session.az_dir, ignore_errors=True)
    session.az_dir = None
    session.az_user = None


class SessionStore:
    def __init__(self, ttl_seconds: int, directory: str | None = None, secret: str = "", msal_factory: Callable[[], Any] | None = None) -> None:
        self._ttl = ttl_seconds
        self._msal_factory = msal_factory  # builds an empty MSAL app, to bring a saved Microsoft sign-in back after a restart
        self._sessions: dict[str, Session] = {}
        self._dir = Path(directory) if directory else None
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())) if secret else None
        if self._dir and self._fernet:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                self._load()
            except OSError:
                logger.warning("Could not use SESSION_DIR; sessions stay in memory only.")
                self._dir = None

    # ---- disk -------------------------------------------------------------------------------
    def _file(self, session_id: str) -> Path | None:
        if not (self._dir and self._fernet) or not session_id.replace("-", "").replace("_", "").isalnum():
            return None
        return self._dir / f"{session_id}.sess"

    def save(self, session: Session) -> None:
        """Write this session to disk (encrypted with SESSION_SECRET). A Microsoft sign-in is saved with its token cache (the refresh
        token), so a restart or a new deployment does not sign anybody out: they stay signed in until they log out."""
        path = self._file(session.id)
        if path is None or self._fernet is None:
            return
        data = {
            "id": session.id, "user": session.user.__dict__, "claude_token": session.claude_token,
            "az_dir": session.az_dir, "az_user": session.az_user, "claude_source": session.claude_source,
            "created_at": session.created_at, "last_seen": session.last_seen,
        }
        if session.msal_app is not None:
            cache = getattr(session.msal_app, "token_cache", None)
            if cache is None or self._msal_factory is None:
                return  # cannot bring it back later: keep it in memory only
            try:
                data["msal_cache"], data["account"] = cache.serialize(), session.account
            except Exception:  # noqa: BLE001 - never let saving break a request
                return
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
            tmp.replace(path)
            session.saved_at = time.time()
        except OSError:
            logger.warning("Could not save a session to disk.")

    def _load(self) -> None:
        assert self._dir is not None and self._fernet is not None
        now = time.time()
        for path in self._dir.glob("*.sess"):
            try:
                d = json.loads(self._fernet.decrypt(path.read_bytes()))
                if now - float(d["last_seen"]) > self._ttl:
                    raise ValueError("expired")
                if d.get("az_dir") and not Path(d["az_dir"]).is_dir():
                    raise ValueError("az folder is gone")
                app: Any = None
                if d.get("msal_cache"):
                    if self._msal_factory is None:
                        raise ValueError("cannot restore a Microsoft sign-in")
                    app = self._msal_factory()
                    app.token_cache.deserialize(d["msal_cache"])
                s = Session(id=d["id"], msal_app=app, account=d.get("account") or {}, user=UserProfile(**d["user"]),
                            claude_token=d.get("claude_token"), az_dir=d.get("az_dir"), az_user=d.get("az_user"),
                            claude_source=d.get("claude_source") or "own", created_at=float(d["created_at"]),
                            last_seen=float(d["last_seen"]))
                self._sessions[s.id] = s
            except (InvalidToken, ValueError, KeyError, TypeError, OSError):
                path.unlink(missing_ok=True)  # unreadable (e.g. SESSION_SECRET changed), expired or broken
        if self._sessions:
            logger.info("Restored %d signed-in session(s).", len(self._sessions))

    def _forget_file(self, session_id: str) -> None:
        path = self._file(session_id)
        if path is not None:
            path.unlink(missing_ok=True)

    # ---- api --------------------------------------------------------------------------------
    def create(self, msal_app: Any, account: dict[str, Any], user: UserProfile) -> Session:
        self._purge()
        session = Session(id=secrets.token_urlsafe(32), msal_app=msal_app, account=account, user=user)
        self._sessions[session.id] = session
        self.save(session)
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        now = time.time()
        if now - session.last_seen > self._ttl:  # idle for the whole TTL
            self.delete(session_id)
            return None
        session.last_seen = now  # activity keeps the person signed in
        if now - session.saved_at > 3600:
            self.save(session)
        return session

    def active(self) -> list[Session]:
        """Sessions that have not expired. Looking at them does NOT count as the person being active."""
        now = time.time()
        return [s for s in self._sessions.values() if now - s.last_seen <= self._ttl]

    def delete(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        self._forget_file(session_id)
        if session is not None:
            cleanup_az(session)

    def _purge(self) -> None:
        now = time.time()
        for sid in [s.id for s in self._sessions.values() if now - s.last_seen > self._ttl]:
            self.delete(sid)


class PendingFlowStore:
    """Holds the MSAL auth-code-flow dicts (state, PKCE verifier, nonce) between /login and /callback."""

    def __init__(self, ttl_seconds: int = FLOW_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._flows: dict[str, tuple[float, dict[str, Any]]] = {}

    def put(self, state: str, flow: dict[str, Any]) -> None:
        self._purge()
        self._flows[state] = (time.time(), flow)

    def pop(self, state: str) -> dict[str, Any] | None:
        """One-time use: a state value can never be redeemed twice."""
        self._purge()
        entry = self._flows.pop(state, None)
        return entry[1] if entry else None

    def _purge(self) -> None:
        now = time.time()
        for state in [s for s, (ts, _) in self._flows.items() if now - ts > self._ttl]:
            self._flows.pop(state, None)


def sign_value(value: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def unsign_value(signed: str | None, secret: str) -> str | None:
    if not signed or "." not in signed:
        return None
    value, _, mac = signed.rpartition(".")
    expected = hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()
    return value if hmac.compare_digest(mac, expected) else None
