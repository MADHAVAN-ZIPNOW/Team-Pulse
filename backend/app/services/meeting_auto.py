"""Automatic processing: each person can switch on "process my finished meetings automatically".

While their Microsoft sign-in is active on the server, this checks their Teams calendar every few minutes. When a meeting THEY ORGANIZED has
ended and Teams has produced a transcript or recording (usually a few minutes later), it is processed exactly as if they had pressed
"Process with Claude": the "Claude, ..." requests are carried out in Azure DevOps (with Undo), and the meeting appears under "My meetings".

Safeguards: opt-in per person; only meetings that ended AFTER the switch was turned on; each meeting once; at most 3 per check; the
person's own sign-in and permissions are used; if their sign-in has expired, nothing happens until they sign in again.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..auth.microsoft_auth import EXCEL_SCOPES, AuthError, MicrosoftAuth
from ..azure_devops.client import AzureDevOpsClient
from ..config import Settings
from . import meeting_import
from .graph_teams import GraphError, GraphTeamsClient
from .meeting_agent import ORG_RE, MeetingAgent
from .session_store import Session, SessionStore

logger = logging.getLogger("pr_assistant.auto")

INTERVAL_SECONDS = 180
MAX_PER_CHECK = 3
GIVE_UP_AFTER = 8  # checks (about 25 minutes): Teams made no transcript or recording, so there is nothing to read
ISO = "%Y-%m-%dT%H:%M:%SZ"


def owner_of(session: Session) -> str:
    u = session.user
    return (u.object_id or u.email or u.name or "").strip().lower()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


class AutoProcessor:
    def __init__(self, agent: MeetingAgent, auth: MicrosoftAuth, sessions: SessionStore, http: httpx.AsyncClient, settings: Settings) -> None:
        self.agent, self.auth, self.sessions, self.http, self.settings = agent, auth, sessions, http, settings
        self._prefs: dict[str, dict[str, Any]] = {}
        self._busy: set[str] = set()

    # ---- the person's switch --------------------------------------------------------------------
    def prefs(self, owner: str) -> dict[str, Any]:
        if owner not in self._prefs:
            self._prefs[owner] = self.agent.store.load_prefs(owner) if self.agent.store is not None else {}
        return self._prefs[owner]

    def _save(self, owner: str) -> None:
        if self.agent.store is not None:
            self.agent.store.save_prefs(owner, self.prefs(owner))

    def excel(self, owner: str) -> str:
        return str(self.prefs(owner).get("excel_url") or "")

    def set_excel(self, owner: str, url: str) -> str:
        p = self.prefs(owner)
        p["excel_url"] = url
        self._save(owner)
        return url

    def status(self, owner: str) -> dict[str, Any]:
        p = self.prefs(owner)
        return {
            "enabled": bool(p.get("auto")), "project": p.get("project", ""), "organization": p.get("organization", ""),
            "enabledAt": p.get("enabled_at"), "lastCheck": p.get("last_check"), "lastError": p.get("last_error", ""),
            "processed": len(p.get("seen", [])),
        }

    def set(self, owner: str, enabled: bool, organization: str, project: str) -> dict[str, Any]:
        p = self.prefs(owner)
        if enabled and not p.get("auto"):
            p.update(auto=True, enabled_at=utcnow(), seen=p.get("seen", []), tries={}, last_error="")  # only meetings that end from now on
        if not enabled:
            p["auto"] = False
        if organization and ORG_RE.match(organization):
            p["organization"] = organization
        p["project"] = project.strip()[:100]
        self._save(owner)
        return self.status(owner)

    def _graph(self, session: Session) -> GraphTeamsClient:
        async def graph_token() -> str:
            return await self.auth.get_graph_token(session)

        async def excel_token() -> str:
            return await self.auth.get_graph_token(session, EXCEL_SCOPES)

        return GraphTeamsClient(self.http, graph_token, excel_token)

    # ---- the check ------------------------------------------------------------------------------
    async def run_forever(self) -> None:
        await asyncio.sleep(20)
        while True:
            for session in self.sessions.active():
                if session.msal_app is None:
                    continue  # only the Microsoft sign-in can read Teams
                try:
                    await self.check(session)
                except Exception:  # noqa: BLE001 - one person's problem must never stop the others
                    logger.exception("Automatic check failed")
            await asyncio.sleep(INTERVAL_SECONDS)

    async def check(self, session: Session) -> None:
        owner = owner_of(session)
        p = self.prefs(owner)
        if not p.get("auto") or owner in self._busy:
            return
        self._busy.add(owner)
        try:
            async def ado_token() -> str:
                return await self.auth.get_access_token(session)

            graph = self._graph(session)
            client = AzureDevOpsClient(self.http, ado_token, api_version=self.settings.azure_devops_api_version)
            try:
                events = await graph.calendar(1, 0)
            except (GraphError, AuthError) as exc:
                p["last_error"] = getattr(exc, "message", str(exc))[:300]
                self._save(owner)
                return
            p["last_check"], p["last_error"] = utcnow(), ""
            seen, tries = set(p.get("seen", [])), p.setdefault("tries", {})
            done = self.agent.by_join_url(owner)
            todo = [
                e for e in events
                if e["state"] == "finished" and e["isOrganizer"] and e["end"] >= p.get("enabled_at", "9999") and e["id"] not in seen and e["joinUrl"] not in done
            ][:MAX_PER_CHECK]
            for e in todo:
                try:
                    online_id = await graph.online_meeting_id(e["joinUrl"])
                    ready = bool(online_id) and await graph.has_content(online_id)
                except (GraphError, AuthError) as exc:
                    p["last_error"] = getattr(exc, "message", str(exc))[:300]
                    continue
                if not ready:  # Teams has not finished its transcript or recording yet: look again next time
                    tries[e["id"]] = tries.get(e["id"], 0) + 1
                    if tries[e["id"]] >= GIVE_UP_AFTER:
                        seen.add(e["id"])
                        tries.pop(e["id"], None)
                    continue
                seen.add(e["id"])
                tries.pop(e["id"], None)
                m = self.agent.create(owner, e["subject"], p.get("organization") or "dteksoftware", p.get("project", ""), source="teams")
                m["join_url"], m["auto"] = e["joinUrl"], True
                self.agent.set_times(m, e.get("start", ""), e.get("end", ""))
                self.agent.set_stage(owner, m["id"], "Starting…")
                await meeting_import.import_teams(self.agent, graph, owner, m["id"], e["joinUrl"], client, self.settings)
            p["seen"] = sorted(seen)[-500:]
            self._save(owner)
        finally:
            self._busy.discard(owner)
