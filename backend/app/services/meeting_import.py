"""Brings a finished meeting into the meeting agent: an uploaded file, or a meeting from the person's Teams calendar.

A transcript (.vtt from Teams, or plain "Name: text") is read directly. A recording (video or audio) goes through ffmpeg and
Azure AI Speech for a better Tamil + English transcript than Teams writes. Either way the lines then go to the agent, which handles
every "Claude, create a PBI ..." request in order (with Undo) and can translate the lines.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ..auth.microsoft_auth import AuthError
from ..azure_devops.client import AzureDevOpsClient
from ..config import Settings
from .graph_teams import GraphError, GraphTeamsClient
from .meeting_agent import MeetingAgent
from . import meeting_log, speech_groq, speech_local
from .speech_azure import MediaError, extract_audio, find_ffmpeg, transcribe
from .transcript_parse import parse_transcript

logger = logging.getLogger("pr_assistant.import")

TEXT_TYPES = {".vtt", ".srt", ".txt", ".md", ".text"}
MEDIA_TYPES = {".mp4", ".m4a", ".mp3", ".wav", ".webm", ".ogg", ".aac", ".flac", ".mkv", ".mov"}


def speech_ready(settings: Settings) -> bool:
    return bool(settings.azure_speech_key and settings.azure_speech_region and find_ffmpeg(settings.ffmpeg_path))


async def _speech_lines(agent: MeetingAgent, owner: str, mid: str, path: str, settings: Settings) -> list[dict[str, Any]]:
    if not (settings.azure_speech_key and settings.azure_speech_region):
        raise MediaError(
            "Reading a recording needs Azure Speech. Add AZURE_SPEECH_KEY and AZURE_SPEECH_REGION to the server's .env, "
            "or import a transcript (.vtt or .txt) instead."
        )
    ffmpeg = find_ffmpeg(settings.ffmpeg_path)
    if not ffmpeg:
        raise MediaError("ffmpeg is not installed on the server (it takes the sound out of a recording). Install it, or import a transcript instead.")
    audio = path + ".mp3"
    try:
        agent.set_stage(owner, mid, "Taking the audio out of the recording…")
        await extract_audio(path, audio, ffmpeg)
        agent.set_stage(owner, mid, "Azure Speech is writing the transcript (Tamil and English)…")
        return await transcribe(audio, settings.azure_speech_key, settings.azure_speech_region, list(settings.speech_locales))
    finally:
        Path(audio).unlink(missing_ok=True)


async def _finish(agent: MeetingAgent, owner: str, mid: str, lines: list[dict[str, Any]], client: AzureDevOpsClient | None) -> None:
    if not lines:
        raise MediaError("No transcript lines were found.")
    await agent.import_lines(owner, mid, lines, client)


def _fail(agent: MeetingAgent, owner: str, mid: str, exc: Exception) -> None:
    message = getattr(exc, "message", None) or str(exc)
    if isinstance(exc, (MediaError, GraphError, AuthError)):
        agent.set_stage(owner, mid, message, True)
    else:
        logger.exception("Importing meeting %s failed", mid[:8])
        agent.set_stage(owner, mid, "Something went wrong while reading that meeting.", True)
    agent.end(owner, mid)  # nothing more is coming: it is not a live meeting


async def import_file(agent: MeetingAgent, owner: str, mid: str, path: str, filename: str, client: AzureDevOpsClient | None, settings: Settings) -> None:
    """Handles an uploaded file. The file is deleted afterwards."""
    try:
        if Path(filename).suffix.lower() in TEXT_TYPES:
            agent.set_stage(owner, mid, "Reading the transcript…")
            lines = parse_transcript(Path(path).read_text(encoding="utf-8", errors="replace"))
        else:
            lines = await _speech_lines(agent, owner, mid, path, settings)
        await _finish(agent, owner, mid, lines, client)
    except Exception as exc:  # noqa: BLE001 - shown to the person, never raised into the server
        _fail(agent, owner, mid, exc)
    finally:
        shutil.rmtree(Path(path).parent, ignore_errors=True)


def local_ready(settings: Settings) -> bool:
    """Can this server turn a recording into text? Groq (fast, needs a key) or Whisper on this PC (free, slower)."""
    ffmpeg = find_ffmpeg(settings.ffmpeg_path)
    return bool(ffmpeg and (speech_groq.available() or (settings.whisper_model and speech_local.available())))


async def _local_transcript(
    agent: MeetingAgent, graph: GraphTeamsClient, owner: str, mid: str, online_id: str, workdir: str,
    client: AzureDevOpsClient | None, settings: Settings,
) -> bool:
    """Recording -> audio (ffmpeg) -> text (Whisper, free, on this server). False when this server cannot do that."""
    if not local_ready(settings):
        return False
    recording = str(Path(workdir) / "recording")
    audio = recording + ".mp3"
    agent.set_stage(owner, mid, "Downloading the recording…")
    if not await graph.download_recording(online_id, recording, settings.meeting_upload_max_mb * 1024 * 1024):
        return False
    agent.set_stage(owner, mid, "Taking the audio out of the recording…")
    await extract_audio(recording, audio, find_ffmpeg(settings.ffmpeg_path) or "ffmpeg")
    Path(recording).unlink(missing_ok=True)
    agent.set_stage(owner, mid, "Writing the transcript from the audio (this takes a few minutes)…")
    loop = asyncio.get_running_loop()
    shown = {"minute": -1}

    def progress(done: float, total: float) -> None:  # runs in the speech thread; show it on the page once per minute of audio
        minute = int(done // 60)
        if minute != shown["minute"]:
            shown["minute"] = minute
            loop.call_soon_threadsafe(agent.set_stage, owner, mid, f"Writing the transcript: {minute} of {int(total // 60) + 1} minutes done…")

    lines: list[dict[str, Any]] = []
    if speech_groq.available():  # the fast way first; if it fails, Whisper on this PC takes over
        try:
            agent.set_stage(owner, mid, "Writing the transcript (Groq, about a minute)…")
            lines = await speech_groq.transcribe(audio, find_ffmpeg(settings.ffmpeg_path) or "ffmpeg")
        except MediaError as exc:
            logger.warning("Groq could not transcribe, using Whisper on this PC: %s", exc)
    if not lines and settings.whisper_model and speech_local.available():
        agent.set_stage(owner, mid, "Writing the transcript from the audio (this takes a few minutes)…")
        lines = await speech_local.transcribe(audio, settings.whisper_model, None, progress)
    if not lines:
        raise MediaError("No speech was found in that recording.")
    await agent.import_lines(owner, mid, lines, client)
    return True


async def _fill_excel(agent: MeetingAgent, graph: GraphTeamsClient, owner: str, mid: str, settings: Settings) -> None:
    """If the person saved an Excel link, Claude lists what was discussed and the Discussion Log is updated."""
    url = agent.excel_url(owner)
    m = agent.get(owner, mid)
    if not url or m is None or not m["lines"] or graph.excel_token is None:
        return
    try:
        agent.set_stage(owner, mid, "Claude is reading the meeting and filling your Excel…")
        excel = meeting_log.ExcelLog(agent_http(graph), graph.excel_token, url)
        await excel.open()
        header, existing = await excel.read()
        topic = next((i for i, h in enumerate(header) if h.strip().lower().startswith("topic")), 0)
        status = next((i for i, h in enumerate(header) if h.strip().lower().startswith("status")), -1)
        known = [(str(r[topic]), str(r[status]) if 0 <= status < len(r) else "") for r in existing if topic < len(r) and str(r[topic]).strip()]
        date = meeting_log.meeting_date(m.get("created_at"))
        rows = await meeting_log.analyze(agent.claude, agent._options(meeting_log.SYSTEM), m["lines"], date, known)
        added, updated = await excel.write(header, existing, rows, date)
        agent.finish_stage(owner, mid, f"Excel updated: {added} new row(s), {updated} row(s) changed.")
    except Exception as exc:  # noqa: BLE001 - the video and transcript are already saved; only the Excel step failed
        logger.warning("Filling the Excel failed: %s", exc)
        say = getattr(exc, "message", None) or str(exc)
        agent.set_stage(owner, mid, f"The transcript is saved, but filling the Excel failed: {say}", True)


def agent_http(graph: GraphTeamsClient) -> httpx.AsyncClient:
    return graph._http


async def import_teams(
    agent: MeetingAgent, graph: GraphTeamsClient, owner: str, mid: str, join_url: str, client: AzureDevOpsClient | None, settings: Settings,
) -> None:
    """Handles a meeting from the person's Teams calendar: the recording through Azure Speech when it is set up, else Teams' transcript."""
    workdir = tempfile.mkdtemp(prefix="meeting-")
    try:
        agent.set_stage(owner, mid, "Finding the meeting in Teams…")
        online_id = await graph.online_meeting_id(join_url)
        if not online_id:
            raise GraphError(
                "Teams would not open that meeting for your account. With a personal sign-in only the organizer can fetch a meeting's "
                "recording or transcript. Ask the organizer, or import the file yourself.", 403)
        recording_id = await graph.latest_recording_id(online_id)
        if recording_id:
            agent.set_recording(owner, mid, online_id, recording_id)  # so the video can be watched in Team Pulse (streamed, not saved)
            if await _local_transcript(agent, graph, owner, mid, online_id, workdir, client, settings):
                await _fill_excel(agent, graph, owner, mid, settings)
            else:  # nothing to read the words with: the recording alone is shown
                agent.set_stage(owner, mid, "")
                agent.end(owner, mid)
            return
        lines: list[dict[str, Any]] = []
        if speech_ready(settings):
            agent.set_stage(owner, mid, "Downloading the recording…")
            recording = str(Path(workdir) / "recording")
            if await graph.download_recording(online_id, recording, settings.meeting_upload_max_mb * 1024 * 1024):
                lines = await _speech_lines(agent, owner, mid, recording, settings)
        if not lines:
            agent.set_stage(owner, mid, "Reading Teams' own transcript…")
            try:
                vtt = await graph.transcript_vtt(online_id)
            except GraphError as exc:
                if exc.status_code == 403 and recording_id:
                    raise MediaError(
                        "Your organization has turned off access to Teams transcripts, so Claude has to read the recording itself. "
                        "That needs Azure Speech (the speech-to-text service) and ffmpeg: add AZURE_SPEECH_KEY and AZURE_SPEECH_REGION "
                        "to the server's .env and install ffmpeg. You can still watch the recording here."
                    ) from exc
                raise
            lines = parse_transcript(vtt) if vtt else []
        if not lines:
            raise MediaError("This meeting has no recording or transcript. Turn on “Record and transcribe” in Teams next time, or import a file.")
        await _finish(agent, owner, mid, lines, client)
    except Exception as exc:  # noqa: BLE001
        _fail(agent, owner, mid, exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
