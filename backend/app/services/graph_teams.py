"""Reads the signed-in person's Teams meetings from Microsoft Graph (their own sign-in, nothing org-wide).

What Microsoft lets a personal sign-in do is decided by Microsoft, not by us: recordings and transcripts normally belong to the
meeting ORGANIZER, and the app needs admin consent for the Teams permissions. When Microsoft says no, the message says why.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

logger = logging.getLogger("pr_assistant.graph")

GRAPH = "https://graph.microsoft.com/v1.0"
TokenProvider = Callable[[], Awaitable[str]]
ISO = "%Y-%m-%dT%H:%M:%SZ"
PARALLEL = 6  # pieces of a recording downloaded at the same time
CHUNK = 8 << 20  # size of one piece (8 MB)


class GraphError(Exception):
    """Message is safe to show to the user."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _why(res: httpx.Response) -> str:
    """Microsoft's own error code and message (for example 'Forbidden: ...'), kept short and safe to show."""
    try:
        err = res.json().get("error") or {}
        return (str(err.get("code") or "") + (": " + str(err.get("message")) if err.get("message") else ""))[:240]
    except ValueError:
        return ""


def _explain(status: int, what: str, why: str = "") -> GraphError:
    if status == 401:
        return GraphError("Your Microsoft session expired. Sign in again.", 401)
    if status == 403:
        return GraphError(
            f"Microsoft did not allow {what}. With a personal sign-in only the meeting organizer can fetch recordings and "
            "transcripts, and your Microsoft 365 admin must have approved the Teams permissions for Team Pulse."
            + (f" (Microsoft says: {why})" if why else ""), 403)
    if status == 429:
        return GraphError("Microsoft is busy. Try again in a minute.", 429)
    return GraphError(f"Microsoft could not {what} (HTTP {status})." + (f" Microsoft says: {why}" if why else ""), 502)


def _iso(value: str) -> str:
    """Graph gives '2026-11-24T10:00:00.0000000' in UTC (we ask for UTC); keep it as a plain ISO time."""
    return value.split(".")[0].rstrip("Z") + "Z"


class GraphTeamsClient:
    def __init__(self, http: httpx.AsyncClient, token_provider: TokenProvider, excel_token: TokenProvider | None = None) -> None:
        self._http = http
        self._token = token_provider
        self.excel_token = excel_token  # a token that may write the person's Excel file (separate permission)

    async def _get(self, path: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None, what: str = "read that") -> httpx.Response:
        try:
            res = await self._http.get(
                GRAPH + path, params=params, headers={"Authorization": f"Bearer {await self._token()}", **(headers or {})}, follow_redirects=True
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GraphError("Could not reach Microsoft (network error or timeout).", 504) from exc
        if res.status_code == 404:
            return res
        if res.status_code >= 400:
            why = _why(res)
            logger.warning("Graph GET %s -> HTTP %s %s", path.split("?")[0], res.status_code, why)
            raise _explain(res.status_code, what, why)
        return res

    async def calendar(self, days_back: int = 14, days_ahead: int = 7) -> list[dict[str, Any]]:
        """Online meetings in the person's calendar: finished, happening now and coming up. Newest first."""
        now = datetime.now(timezone.utc)
        now_iso = now.strftime(ISO)
        res = await self._get(
            "/me/calendarView",
            {"startDateTime": (now - timedelta(days=days_back)).strftime(ISO), "endDateTime": (now + timedelta(days=days_ahead)).strftime(ISO),
             "$top": "200", "$select": "id,subject,start,end,isOnlineMeeting,onlineMeeting,organizer,isCancelled,isOrganizer"},
            {"Prefer": 'outlook.timezone="UTC"'}, "read your calendar",
        )
        out = []
        for ev in res.json().get("value") or []:
            join = ((ev.get("onlineMeeting") or {}).get("joinUrl") or "").strip()
            if ev.get("isCancelled") or not ev.get("isOnlineMeeting") or not join:
                continue
            start, end = _iso(ev["start"]["dateTime"]), _iso(ev["end"]["dateTime"])
            out.append({
                "id": ev["id"], "subject": str(ev.get("subject") or "Meeting")[:200], "start": start, "end": end, "joinUrl": join,
                "state": "finished" if end <= now_iso else "live" if start <= now_iso else "upcoming",
                "isOrganizer": bool(ev.get("isOrganizer")),
                "organizer": str(((ev.get("organizer") or {}).get("emailAddress") or {}).get("name") or "")[:100],
            })
        return sorted(out, key=lambda e: e["start"], reverse=True)

    async def online_meeting_id(self, join_url: str) -> str | None:
        flt = "JoinWebUrl eq '" + join_url.replace("'", "''") + "'"
        res = await self._get("/me/onlineMeetings", {"$filter": flt}, what="open that meeting")
        items = res.json().get("value") or [] if res.status_code == 200 else []
        return str(items[0]["id"]) if items else None

    async def _latest(self, meeting_id: str, kind: str) -> str | None:
        res = await self._get(f"/me/onlineMeetings/{quote(meeting_id, safe='')}/{kind}", what=f"list the {kind}")
        items = res.json().get("value") or [] if res.status_code == 200 else []
        items.sort(key=lambda i: i.get("createdDateTime") or "")
        return str(items[-1]["id"]) if items else None

    async def latest_recording_id(self, meeting_id: str) -> str | None:
        return await self._latest(meeting_id, "recordings")

    async def open_recording(self, meeting_id: str, recording_id: str, range_header: str | None = None) -> httpx.Response:
        """Opens the recording as a stream (the caller reads it and closes it). Passes the browser's Range header through, so the
        video can be played and skipped through without ever being saved on the server."""
        url = f"{GRAPH}/me/onlineMeetings/{quote(meeting_id, safe='')}/recordings/{quote(recording_id, safe='')}/content"
        headers = {"Authorization": f"Bearer {await self._token()}"}
        if range_header:
            headers["Range"] = range_header
        try:
            res = await self._http.send(self._http.build_request("GET", url, headers=headers), stream=True, follow_redirects=True)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GraphError("Could not reach Microsoft (network error or timeout).", 504) from exc
        if res.status_code >= 400 and res.status_code != 416:
            await res.aclose()
            raise _explain(res.status_code, "open the recording")
        return res

    async def has_content(self, meeting_id: str) -> bool:
        """True once the meeting has a transcript or a recording (they appear a few minutes after it ends)."""
        return bool(await self._latest(meeting_id, "transcripts") or await self._latest(meeting_id, "recordings"))

    async def transcript_vtt(self, meeting_id: str) -> str | None:
        """Teams' own transcript of the meeting as WebVTT, or None when it has none."""
        tid = await self._latest(meeting_id, "transcripts")
        if not tid:
            return None
        res = await self._get(f"/me/onlineMeetings/{quote(meeting_id, safe='')}/transcripts/{quote(tid, safe='')}/content", {"$format": "text/vtt"}, what="read the transcript")
        return res.text if res.status_code == 200 else None

    async def download_recording(self, meeting_id: str, dst: str, max_bytes: int) -> bool:
        """Saves the newest recording to dst. False when the meeting has none.

        It first tries to fetch the file in several pieces at the same time (much faster than one connection when the server
        limits each connection), and falls back to one connection if the server does not allow that."""
        rid = await self._latest(meeting_id, "recordings")
        if not rid:
            return False
        url = f"{GRAPH}/me/onlineMeetings/{quote(meeting_id, safe='')}/recordings/{quote(rid, safe='')}/content"
        try:
            got = await self._download_parallel(url, dst, max_bytes)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            Path(dst).unlink(missing_ok=True)
            logger.info("Parallel recording download failed (%s); using one connection", type(exc).__name__)
            got = None
        if got is not None:
            return got
        Path(dst).unlink(missing_ok=True)
        return await self._download_single(url, dst, max_bytes)

    async def _download_parallel(self, url: str, dst: str, max_bytes: int) -> bool | None:
        """True = saved, False = no such recording, None = this server does not allow ranges (use one connection)."""
        auth = {"Authorization": f"Bearer {await self._token()}"}
        first = await self._http.get(url, headers={**auth, "Range": "bytes=0-0"}, follow_redirects=True)
        if first.status_code == 404:
            return False
        if first.status_code >= 400:
            raise _explain(first.status_code, "download the recording")
        crange = first.headers.get("content-range", "")
        if first.status_code != 206 or "/" not in crange or not crange.rsplit("/", 1)[1].isdigit():
            return None
        total = int(crange.rsplit("/", 1)[1])
        if total > max_bytes:
            raise GraphError("That recording is too large for this server's upload limit.", 413)
        # the redirect target is usually a pre-signed storage URL that needs no token; keep the token only for Microsoft Graph itself
        final = str(first.url)
        headers = auth if final.startswith(GRAPH) else {}
        spans = [(i, min(i + CHUNK, total) - 1) for i in range(0, total, CHUNK)]
        with open(dst, "wb") as f:
            f.truncate(total)
        gate = asyncio.Semaphore(PARALLEL)

        async def piece(start: int, end: int) -> None:
            async with gate:
                for attempt in range(3):
                    res = await self._http.get(final, headers={**headers, "Range": f"bytes={start}-{end}"}, follow_redirects=True)
                    if res.status_code == 206 and len(res.content) == end - start + 1:
                        with open(dst, "r+b") as f:
                            f.seek(start)
                            f.write(res.content)
                        return
                    if res.status_code in (401, 403) and headers is not auth:
                        headers.update(auth)  # the storage URL wanted the token after all
                raise GraphError("The recording download was interrupted. Try again.", 502)

        await asyncio.gather(*(piece(a, b) for a, b in spans))
        return True

    async def _download_single(self, url: str, dst: str, max_bytes: int) -> bool:
        try:
            async with self._http.stream("GET", url, headers={"Authorization": f"Bearer {await self._token()}"}, follow_redirects=True) as res:
                if res.status_code == 404:
                    return False
                if res.status_code >= 400:
                    raise _explain(res.status_code, "download the recording")
                size = 0
                with open(dst, "wb") as f:
                    async for chunk in res.aiter_bytes(1 << 20):
                        size += len(chunk)
                        if size > max_bytes:
                            raise GraphError("That recording is too large for this server's upload limit.", 413)
                        f.write(chunk)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            Path(dst).unlink(missing_ok=True)
            raise GraphError("Could not download the recording (network error or timeout).", 504) from exc
        return True
