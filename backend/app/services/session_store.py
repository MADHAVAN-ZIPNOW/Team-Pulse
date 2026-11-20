"""In-memory, server-side session storage. Tokens never leave this process."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import shutil
import time
from dataclasses import dataclass, field
from typing import Any

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
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def cleanup_az(session: "Session") -> None:
    """Remove the person's az sign-in folder (tokens) from disk."""
    if session.az_dir:
        shutil.rmtree(session.az_dir, ignore_errors=True)
    session.az_dir = None
    session.az_user = None


class SessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def create(self, msal_app: Any, account: dict[str, Any], user: UserProfile) -> Session:
        self._purge()
        session = Session(id=secrets.token_urlsafe(32), msal_app=msal_app, account=account, user=user)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() - session.created_at > self._ttl:
            self._sessions.pop(session_id, None)
            return None
        return session

    def delete(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            cleanup_az(session)

    def _purge(self) -> None:
        now = time.time()
        for sid in [s.id for s in self._sessions.values() if now - s.created_at > self._ttl]:
            expired = self._sessions.pop(sid, None)
            if expired is not None:
                cleanup_az(expired)


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
