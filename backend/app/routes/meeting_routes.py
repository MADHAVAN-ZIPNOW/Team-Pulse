"""Meeting agent: live meeting lines in, Azure DevOps work items out. Every meeting belongs to the signed-in person who made it.

Off unless MEETING_AGENT_ENABLED=true, because it WRITES to Azure DevOps (everything else in this app only reads).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from ..auth.dependencies import get_ado_client, get_auth, get_current_session, get_http_client, require_same_origin
from ..auth.microsoft_auth import EXCEL_SCOPES, AuthError, MicrosoftAuth
from ..azure_devops.client import AzureDevOpsClient
from ..claude.claude_code_service import ClaudeCodeError
from ..config import Settings, get_settings
from ..services import meeting_import
from ..services.graph_teams import GraphError, GraphTeamsClient
from ..services.meeting_agent import MAX_LINE, ORG_RE, MeetingAgent
from ..services.speech_azure import find_ffmpeg
from ..services.rate_limit import RateLimiter, rate_limit_dependency
from ..services.session_store import Session

router = APIRouter(prefix="/api/meetings", tags=["meetings"], dependencies=[Depends(get_current_session)])
_line_limiter = RateLimiter(max_requests=240, window_seconds=60)
_write_limiter = RateLimiter(max_requests=60, window_seconds=60)


def _agent(request: Request) -> MeetingAgent:
    agent = getattr(request.app.state, "meetings", None)
    if agent is None:
        raise HTTPException(status_code=404, detail="The meeting agent is turned off on this server (MEETING_AGENT_ENABLED).")
    return agent


def _owner(session: Session) -> str:
    u = session.user
    return (u.object_id or u.email or u.name or "").strip().lower()


def _meeting(agent: MeetingAgent, session: Session, meeting_id: str) -> dict[str, Any]:
    m = agent.get(_owner(session), meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    return m


class NewMeeting(BaseModel):
    title: str = Field(default="", max_length=120)
    organization: str = Field(min_length=1, max_length=64)
    project: str = Field(default="", max_length=100)


class NewLine(BaseModel):
    speaker: str = Field(default="Speaker", max_length=60)
    text: str = Field(min_length=1, max_length=MAX_LINE)


class Project(BaseModel):
    project: str = Field(max_length=100)


class TeamsImport(BaseModel):
    joinUrl: str = Field(min_length=10, max_length=2000)
    subject: str = Field(default="", max_length=200)
    organization: str = Field(min_length=1, max_length=64)
    project: str = Field(default="", max_length=100)
    start: str = Field(default="", max_length=30)  # the meeting's real start and end (from the calendar)
    end: str = Field(default="", max_length=30)


class AutoBody(BaseModel):
    enabled: bool
    organization: str = Field(default="", max_length=64)
    project: str = Field(default="", max_length=100)


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class TranslateBody(BaseModel):
    target: Literal["auto", "ta", "en"] = "auto"


def _graph(
    session: Annotated[Session, Depends(get_current_session)], auth: Annotated[MicrosoftAuth, Depends(get_auth)],
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> GraphTeamsClient:
    async def token() -> str:
        return await auth.get_graph_token(session)  # raises AuthError with a clear reason (for example: admin has not approved yet)

    async def excel_token() -> str:
        return await auth.get_graph_token(session, EXCEL_SCOPES)

    return GraphTeamsClient(http, token, excel_token)


def _say(exc: Exception) -> HTTPException:
    return HTTPException(status_code=getattr(exc, "status_code", 502), detail=getattr(exc, "message", None) or str(exc))


@router.get("/status")
async def status(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, bool]:
    return {
        "enabled": getattr(request.app.state, "meetings", None) is not None,
        "speech": bool(settings.azure_speech_key and settings.azure_speech_region),
        "ffmpeg": find_ffmpeg(settings.ffmpeg_path) is not None,
        "whisper": meeting_import.local_ready(settings),
        "teams": settings.auth_mode == "msal",
    }


def _auto(request: Request) -> Any:
    auto = getattr(request.app.state, "auto", None)
    if auto is None:
        raise HTTPException(status_code=400, detail="Automatic processing needs the 'Sign in with Microsoft' sign-in and the meeting agent switched on.")
    return auto


@router.get("/auto")
async def auto_status(request: Request, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, Any]:
    """Whether this person has switched on automatic processing of their finished Teams meetings."""
    _agent(request)
    auto = getattr(request.app.state, "auto", None)
    return {"available": auto is not None, **(auto.status(_owner(session)) if auto is not None else {"enabled": False})}


class ExcelLink(BaseModel):
    url: str = Field(default="", max_length=2000)


_EXCEL_HOSTS = ("sharepoint.com", "1drv.ms", "onedrive.live.com")


@router.get("/excel")
async def excel_get(request: Request, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, Any]:
    auto = _auto(request)
    return {"available": auto is not None, "url": auto.excel(_owner(session)) if auto is not None else ""}


@router.post("/excel", dependencies=[Depends(require_same_origin)])
async def excel_set(request: Request, body: ExcelLink, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, Any]:
    """Where the Discussion Log lives. Claude fills it after each meeting you organize."""
    auto = _auto(request)
    if auto is None:
        raise HTTPException(status_code=400, detail="This needs the Microsoft sign-in and the meeting agent switched on.")
    url = body.url.strip()
    if url:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in _EXCEL_HOSTS):
            raise HTTPException(status_code=422, detail="Paste the Excel share link from OneDrive or SharePoint (it starts with https://…sharepoint.com).")
    return {"available": True, "url": auto.set_excel(_owner(session), url)}


@router.post("/auto", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "meeting-auto"))])
async def auto_set(
    request: Request, body: AutoBody, session: Annotated[Session, Depends(get_current_session)],
    graph: Annotated[GraphTeamsClient, Depends(_graph)],
) -> dict[str, Any]:
    """Turn automatic processing on or off. Turning it on first checks that Teams can really be read, so a problem shows up now."""
    _agent(request)
    auto = _auto(request)
    if body.enabled:
        try:
            await graph.calendar(1, 0)
        except (GraphError, AuthError) as exc:
            raise _say(exc) from exc
    return auto.set(_owner(session), body.enabled, body.organization, body.project)


@router.get("/teams/calendar")
async def teams_calendar(
    request: Request, session: Annotated[Session, Depends(get_current_session)], graph: Annotated[GraphTeamsClient, Depends(_graph)],
    back: int = Query(default=14, ge=1, le=60), ahead: int = Query(default=7, ge=0, le=30),
) -> list[dict[str, Any]]:
    """The signed-in person's own Teams meetings (finished, now, coming up), marked when Claude has already read them."""
    agent = _agent(request)
    try:
        items = await graph.calendar(back, ahead)
    except (GraphError, AuthError) as exc:
        raise _say(exc) from exc
    done = agent.by_join_url(_owner(session))
    return [{**i, "processedId": done.get(i["joinUrl"])} for i in items]


@router.post("/teams/import", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "meeting-teams"))])
async def teams_import(
    request: Request, body: TeamsImport, session: Annotated[Session, Depends(get_current_session)],
    graph: Annotated[GraphTeamsClient, Depends(_graph)], client: Annotated[AzureDevOpsClient, Depends(get_ado_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Process a Teams meeting with Claude: fetch its recording or transcript, then handle the requests made in it."""
    agent = _agent(request)
    if settings.auth_mode != "msal":
        raise HTTPException(status_code=400, detail="Teams meetings need the 'Sign in with Microsoft' sign-in.")
    if not ORG_RE.match(body.organization) or not body.joinUrl.startswith("https://"):
        raise HTTPException(status_code=422, detail="Invalid request: check organization and joinUrl.")
    m = agent.create(_owner(session), body.subject or "Teams meeting", body.organization, body.project, source="teams")
    m["join_url"] = body.joinUrl
    if body.start:
        agent.set_times(m, body.start, body.end)
    agent.set_stage(_owner(session), m["id"], "Starting…")
    task = asyncio.create_task(meeting_import.import_teams(agent, graph, _owner(session), m["id"], body.joinUrl, client, settings))
    agent._tasks.add(task)
    task.add_done_callback(agent._tasks.discard)
    return agent.public(m)


@router.post("/import", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "meeting-import"))])
async def import_upload(
    request: Request, session: Annotated[Session, Depends(get_current_session)], client: Annotated[AzureDevOpsClient, Depends(get_ado_client)],
    settings: Annotated[Settings, Depends(get_settings)], filename: str = Query(min_length=1, max_length=200),
    organization: str = Query(min_length=1, max_length=64), project: str = Query(default="", max_length=100),
) -> dict[str, Any]:
    """Upload a transcript (.vtt / .txt) or a recording (.mp4, .m4a, .mp3 ...) as the request body. Claude then handles the requests in it."""
    agent = _agent(request)
    suffix = Path(filename).suffix.lower()
    if suffix not in meeting_import.TEXT_TYPES | meeting_import.MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="Use a transcript (.vtt, .txt) or a recording (.mp4, .m4a, .mp3, .wav).")
    if not ORG_RE.match(organization):
        raise HTTPException(status_code=422, detail="Invalid request: check organization.")
    limit = settings.meeting_upload_max_mb * 1024 * 1024
    workdir = tempfile.mkdtemp(prefix="meeting-")
    path = Path(workdir) / f"upload{suffix}"
    size = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"That file is larger than {settings.meeting_upload_max_mb} MB.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="The file is empty.")
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    m = agent.create(_owner(session), (Path(filename).stem[:100] or "Imported meeting"), organization, project, source="import")
    agent.set_stage(_owner(session), m["id"], "Starting…")
    task = asyncio.create_task(meeting_import.import_file(agent, _owner(session), m["id"], str(path), filename, client, settings))
    agent._tasks.add(task)
    task.add_done_callback(agent._tasks.discard)
    return agent.public(m)


@router.get("")
async def list_meetings(request: Request, session: Annotated[Session, Depends(get_current_session)]) -> list[dict[str, Any]]:
    return _agent(request).list(_owner(session))


@router.post("", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "meeting-new"))])
async def new_meeting(request: Request, body: NewMeeting, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, Any]:
    agent = _agent(request)
    if not ORG_RE.match(body.organization):
        raise HTTPException(status_code=422, detail="Invalid request: check organization.")
    return agent.public(agent.create(_owner(session), body.title, body.organization, body.project))


@router.get("/{meeting_id}")
async def get_meeting(request: Request, meeting_id: str, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, Any]:
    agent = _agent(request)
    return agent.public(_meeting(agent, session, meeting_id))


@router.delete("/{meeting_id}", dependencies=[Depends(require_same_origin)])
async def delete_meeting(request: Request, meeting_id: str, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, bool]:
    if not _agent(request).delete(_owner(session), meeting_id):
        raise HTTPException(status_code=404, detail="Meeting not found.")
    return {"deleted": True}


@router.post("/{meeting_id}/project", dependencies=[Depends(require_same_origin)])
async def set_project(request: Request, meeting_id: str, body: Project, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, str]:
    if not _agent(request).set_project(_owner(session), meeting_id, body.project):
        raise HTTPException(status_code=404, detail="Meeting not found.")
    return {"project": body.project.strip()}


@router.post("/{meeting_id}/translate", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "meeting-translate"))])
async def translate(request: Request, meeting_id: str, body: TranslateBody, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, bool]:
    """Translate the transcript between Tamil and English in the background; the lines update live."""
    agent = _agent(request)
    _meeting(agent, session, meeting_id)
    task = asyncio.create_task(agent.translate(_owner(session), meeting_id, body.target))
    agent._tasks.add(task)
    task.add_done_callback(agent._tasks.discard)
    return {"started": True}


@router.post("/{meeting_id}/ask", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "meeting-ask"))])
async def ask(request: Request, meeting_id: str, body: Ask, session: Annotated[Session, Depends(get_current_session)]) -> dict[str, str]:
    """Ask Claude about this meeting (summary, action items, decisions ...). It only reads the transcript; it never creates anything."""
    agent = _agent(request)
    _meeting(agent, session, meeting_id)
    try:
        return {"answer": await agent.ask(_owner(session), meeting_id, body.question.strip())}
    except ClaudeCodeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{meeting_id}/end", dependencies=[Depends(require_same_origin)])
async def end_meeting(
    request: Request, meeting_id: str, session: Annotated[Session, Depends(get_current_session)],
    graph: Annotated[GraphTeamsClient, Depends(_graph)], settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, bool]:
    agent = _agent(request)
    m = agent.get(_owner(session), meeting_id)
    live = m is not None and not m["ended_at"] and m["source"] not in ("teams", "import") and bool(m["lines"])
    if not agent.end(_owner(session), meeting_id):
        raise HTTPException(status_code=404, detail="Meeting not found.")
    if live and settings.auth_mode == "msal" and agent.excel_url(_owner(session)):  # a meeting spoken live (browser captions): fill the Excel when it ends
        agent.set_stage(_owner(session), meeting_id, "Claude is reading the meeting and filling your Excel…")
        task = asyncio.create_task(meeting_import._fill_excel(agent, graph, _owner(session), meeting_id, settings))
        agent._tasks.add(task)
        task.add_done_callback(agent._tasks.discard)
    return {"ended": True}


@router.post("/{meeting_id}/fill-excel", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_write_limiter, "meeting-excel"))])
async def fill_excel_again(
    request: Request, meeting_id: str, session: Annotated[Session, Depends(get_current_session)],
    graph: Annotated[GraphTeamsClient, Depends(_graph)], settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, bool]:
    """Claude reads the SAVED transcript again and updates the Excel (no download, nothing stored)."""
    agent = _agent(request)
    m = agent.get(_owner(session), meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    if not m["lines"]:
        raise HTTPException(status_code=409, detail="This meeting has no text yet.")
    if m.get("stage"):
        raise HTTPException(status_code=409, detail="Claude is still working on this meeting.")
    if settings.auth_mode != "msal" or not agent.excel_url(_owner(session)):
        raise HTTPException(status_code=400, detail="Save your Excel link on the Meetings page first.")
    agent.set_stage(_owner(session), meeting_id, "Claude is reading the meeting and filling your Excel…")
    task = asyncio.create_task(meeting_import._fill_excel(agent, graph, _owner(session), meeting_id, settings))
    agent._tasks.add(task)
    task.add_done_callback(agent._tasks.discard)
    return {"started": True}


@router.post("/{meeting_id}/lines", dependencies=[Depends(require_same_origin), Depends(rate_limit_dependency(_line_limiter, "meeting-line"))])
async def add_line(
    request: Request, meeting_id: str, body: NewLine, session: Annotated[Session, Depends(get_current_session)],
    client: Annotated[AzureDevOpsClient, Depends(get_ado_client)],
) -> dict[str, Any]:
    """One spoken line. If it addresses Claude, Claude works out the request and creates the work items as this person."""
    line = await _agent(request).add_line(_owner(session), meeting_id, body.speaker, body.text, client)
    if line is None:
        raise HTTPException(status_code=409, detail="This meeting has ended or cannot take more lines.")
    return line


@router.post("/{meeting_id}/actions/{action_id}/undo", dependencies=[Depends(require_same_origin)])
async def undo(
    request: Request, meeting_id: str, action_id: str, session: Annotated[Session, Depends(get_current_session)],
    client: Annotated[AzureDevOpsClient, Depends(get_ado_client)],
) -> dict[str, Any]:
    done = await _agent(request).undo(_owner(session), meeting_id, action_id, client)
    if done is None:
        raise HTTPException(status_code=404, detail="Nothing to undo.")
    return done


@router.get("/{meeting_id}/stream")
async def stream(request: Request, meeting_id: str, session: Annotated[Session, Depends(get_current_session)]) -> StreamingResponse:
    """Server-sent events: {"type": "line"|"action"|"end", ...} as the meeting goes on."""
    agent = _agent(request)
    _meeting(agent, session, meeting_id)  # 404 unless this person owns it

    async def events():  # type: ignore[no-untyped-def]
        q = agent.subscribe(meeting_id)
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            agent.unsubscribe(meeting_id, q)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/{meeting_id}/video")
async def video(
    request: Request, meeting_id: str, session: Annotated[Session, Depends(get_current_session)],
    graph: Annotated[GraphTeamsClient, Depends(_graph)],
) -> StreamingResponse:
    """Plays the meeting's Teams recording. It is streamed from Microsoft with the person's own sign-in and never saved here."""
    agent = _agent(request)
    rec = _meeting(agent, session, meeting_id).get("recording")
    if not rec:
        raise HTTPException(status_code=404, detail="This meeting has no recording to play here.")
    try:
        res = await graph.open_recording(rec["online_id"], rec["recording_id"], request.headers.get("range"))
    except (GraphError, AuthError) as exc:
        raise _say(exc) from exc
    keep = ("content-length", "content-range", "accept-ranges", "content-type")
    headers = {k: v for k, v in res.headers.items() if k.lower() in keep}
    headers.setdefault("Accept-Ranges", "bytes")
    return StreamingResponse(
        res.aiter_raw(), status_code=res.status_code, headers=headers,
        media_type=res.headers.get("content-type", "video/mp4"), background=BackgroundTask(res.aclose),
    )


@router.get("/{meeting_id}/export", response_class=PlainTextResponse)
async def export(request: Request, meeting_id: str, session: Annotated[Session, Depends(get_current_session)]) -> PlainTextResponse:
    agent = _agent(request)
    m = _meeting(agent, session, meeting_id)
    return PlainTextResponse(agent.export(m), headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id[:8]}.txt"'})
