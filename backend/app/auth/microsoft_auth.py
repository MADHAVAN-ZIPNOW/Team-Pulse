"""Microsoft Entra ID auth-code flow with PKCE, built on MSAL Python."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import msal

from ..config import AZURE_DEVOPS_SCOPE, Settings
from ..services.session_store import PendingFlowStore, Session, SessionStore, UserProfile

logger = logging.getLogger("pr_assistant.auth")

# openid/profile/offline_access are added by MSAL automatically; only list the resource scope.
SCOPES = [AZURE_DEVOPS_SCOPE]
# Teams meetings (Microsoft Graph). These are asked for later, silently, never at sign-in: they need admin consent, and asking
# for them at sign-in would stop everyone from signing in until the admin approves.
GRAPH_SCOPES = [
    "https://graph.microsoft.com/Calendars.Read", "https://graph.microsoft.com/OnlineMeetings.Read",
    "https://graph.microsoft.com/OnlineMeetingTranscript.Read.All", "https://graph.microsoft.com/OnlineMeetingRecording.Read.All",
]
# Writing the person's Excel file. Asked for separately from the Teams scopes above, so a missing approval here can never break the video.
EXCEL_SCOPES = ["https://graph.microsoft.com/Files.ReadWrite"]


class AuthError(Exception):
    """Raised with a message that is safe to show to the user."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class MicrosoftAuth:
    def __init__(self, settings: Settings, sessions: SessionStore, flows: PendingFlowStore) -> None:
        self.settings = settings
        self.sessions = sessions
        self.flows = flows
        self._graph_cache: dict[str, tuple[str, float]] = {}  # session id -> (Teams token, expires at); memory only

    def _build_app(self) -> Any:
        if missing := self.settings.missing_auth_config():
            raise AuthError(f"Server is missing configuration: {', '.join(missing)}", 503)
        return msal.ConfidentialClientApplication(
            self.settings.client_id,
            client_credential=self.settings.client_secret,
            authority=self.settings.authority,
            token_cache=msal.SerializableTokenCache(),
        )

    def start_login(self) -> tuple[str, str]:
        """Returns (authorization_url, state). MSAL generates state, nonce and the PKCE pair."""
        app = self._build_app()
        flow = app.initiate_auth_code_flow(
            SCOPES, redirect_uri=self.settings.redirect_uri, prompt="select_account"
        )
        if "auth_uri" not in flow or "state" not in flow:
            logger.error("Could not initiate auth code flow: %s", flow.get("error"))
            raise AuthError("Could not start Microsoft sign-in.", 502)
        self.flows.put(flow["state"], flow)
        return flow["auth_uri"], flow["state"]

    def complete_login(self, query_params: dict[str, str], cookie_state: str | None) -> Session:
        """Validates state (query + browser-bound cookie), redeems the code, creates a session."""
        state = query_params.get("state")
        if not state or not cookie_state or state != cookie_state:
            raise AuthError("Sign-in state mismatch. Please try signing in again.", 400)
        flow = self.flows.pop(state)
        if flow is None:
            raise AuthError("Sign-in expired or already used. Please try again.", 400)
        if "error" in query_params:
            logger.warning("Entra returned error: %s", query_params.get("error"))
            raise AuthError("Microsoft sign-in was cancelled or denied.", 400)

        app = self._build_app()
        try:
            # Validates state + nonce, sends the PKCE code_verifier with the token request.
            result = app.acquire_token_by_auth_code_flow(flow, query_params, scopes=SCOPES)
        except ValueError as exc:  # state mismatch inside MSAL
            logger.warning("Auth code flow rejected: %s", exc)
            raise AuthError("Sign-in could not be validated. Please try again.", 400) from exc
        if "access_token" not in result:
            logger.warning("Token exchange failed: %s", result.get("error"))
            raise AuthError("Microsoft sign-in failed. Check the app registration and permissions.", 401)

        claims = result.get("id_token_claims") or {}
        accounts = app.get_accounts()
        wanted = (result.get("account") or {}).get("home_account_id")
        account = next(
            (a for a in accounts if a.get("home_account_id") == wanted),
            accounts[0] if accounts else None,
        )
        if account is None:
            raise AuthError("Sign-in succeeded but no account was returned.", 401)
        user = UserProfile(
            name=claims.get("name") or claims.get("preferred_username") or "Unknown user",
            email=claims.get("preferred_username") or claims.get("email"),
            tenant_id=claims.get("tid"),
            object_id=claims.get("oid"),
        )
        allowed = self.settings.allowed_users
        if allowed and (user.email or "").lower() not in allowed:
            logger.warning("Sign-in rejected: user is not in ALLOWED_USERS")
            raise AuthError("Your account is not allowed to use this app. Ask the owner to add you.", 403)
        return self.sessions.create(app, account, user)

    async def get_graph_token(self, session: Session, scopes: list[str] | None = None) -> str:
        """A Microsoft Graph token for this person's Teams meetings. Needs admin consent for the Teams permissions."""
        if session.msal_app is None:
            raise AuthError("Teams meetings need the 'Sign in with Microsoft' sign-in (not the Azure CLI code sign-in).", 400)
        key = session.id + ("|" + ",".join(scopes) if scopes else "")
        hit = self._graph_cache.get(key)
        if hit and hit[1] - 120 > time.time():
            return hit[0]

        def acquire() -> dict[str, Any] | None:
            # Work on a private copy of the sign-in cache: a failed Teams attempt can then never damage the real sign-in.
            cache = msal.SerializableTokenCache()
            cache.deserialize(session.msal_app.token_cache.serialize())
            c = self.settings
            app = msal.ConfidentialClientApplication(c.client_id, client_credential=c.client_secret, authority=c.authority, token_cache=cache)
            home = (session.account or {}).get("home_account_id")
            account = next((a for a in app.get_accounts() if a.get("home_account_id") == home), None) or session.account
            return app.acquire_token_silent_with_error(scopes or GRAPH_SCOPES, account=account)  # unlike acquire_token_silent, this says WHY it failed

        async with session.lock:
            result = await asyncio.to_thread(acquire)
        if result and "access_token" in result:
            self._graph_cache[key] = (result["access_token"], time.time() + int(result.get("expires_in", 3000)))
        if not result or "access_token" not in result:
            detail = str((result or {}).get("error_description") or (result or {}).get("error") or "no Graph token in this sign-in yet")
            logger.warning("Teams (Graph) token not available: %s", detail.replace("\n", " ")[:400])
            code = next((w.strip(":.,") for w in detail.split() if w.startswith("AADSTS")), "")
            c = self.settings
            link = f"https://login.microsoftonline.com/{c.tenant_id}/adminconsent?client_id={c.client_id}" if c.tenant_id and c.client_id else ""
            raise AuthError(
                "Teams meetings are not connected yet: your Microsoft 365 admin has to approve the Teams permissions for Team Pulse "
                "(Calendars.Read, OnlineMeetings.Read, OnlineMeetingTranscript.Read.All, OnlineMeetingRecording.Read.All)."
                + (f" (Microsoft's code: {code}.)" if code else "")
                + (f" Admin approval link: {link}" if link else ""), 403)
        return result["access_token"]

    async def get_access_token(self, session: Session) -> str:
        """Returns a valid Azure DevOps access token; MSAL refreshes it from the cached refresh token."""
        if session.msal_app is None:  # Azure CLI mode
            from ..services import azure_cli

            try:
                return await azure_cli.get_access_token(session.az_dir)
            except azure_cli.AzureCliError as exc:
                self.sessions.delete(session.id)
                raise AuthError(str(exc), 401) from exc
        async with session.lock:
            result = await asyncio.to_thread(
                session.msal_app.acquire_token_silent, SCOPES, account=session.account
            )
        if not result or "access_token" not in result:
            self.sessions.delete(session.id)
            raise AuthError("Your Microsoft session expired. Please sign in again.", 401)
        return result["access_token"]
