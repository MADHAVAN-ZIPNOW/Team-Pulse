"""Fills the person's Excel "Discussion Log" (on OneDrive/SharePoint) from what was said in a meeting.

Claude reads the transcript and lists each topic: its summary, type, priority, decision, next action, owner, due date and status.
New topics become new rows; a topic that is already in the sheet is updated (only the cells Claude has something for - an existing
value is never blanked). Everything is written with the person's own Microsoft sign-in.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

from ..claude.claude_code_service import ClaudeCodeError
from .graph_teams import GraphError, _explain

logger = logging.getLogger("pr_assistant.log")

GRAPH = "https://graph.microsoft.com/v1.0"
SHEET = "Discussion Log"
# our field -> the start of the Excel column header (compared in lower case)
COLUMNS = {
    "date": "date", "topic": "topic", "summary": "discussion", "type": "type", "priority": "priority", "decision": "decision",
    "next_action": "next action", "owner": "owner", "due_date": "due", "status": "status", "link": "reference",
}
UPDATABLE = ("summary", "priority", "decision", "next_action", "owner", "due_date", "status", "link")
KEYS = ("topic", "summary", "type", "priority", "decision", "next_action", "owner", "due_date", "status", "link", "update_of")
MAX_TRANSCRIPT = 60_000

SYSTEM = "You turn a meeting transcript into rows of a discussion log. Answer with a JSON array only, no other text."
PROMPT = """This is a transcript of a team meeting held on {date} (speech-to-text, so words may be wrong; it may mix Tamil and English).
Write what was discussed as rows of a discussion log.

LANGUAGE (very important): write ONLY in Tamil script (தமிழ்) and English. NEVER write Tanglish, that is, Tamil words spelled in English letters.
- What was said in Tamil is written in proper Tamil script. What was said in English is written in English. A mixed discussion may mix both.
- Technical and product words that people say in English (PBI, API, sprint, Azure DevOps, deployment, release, bug...) stay in English letters inside the Tamil sentence.
- If the transcript shows Tamil spelled in English letters, understand it and write it in Tamil script.
- People's names stay as they were said, in English letters.
- This applies to "topic", "summary", "decision", "next_action" and "owner". Keep "type", "priority", "status" and "due_date" exactly as the English values below,
  because the Excel sheet uses them as fixed choices.
- When a row is in Tamil, write its prefix in Tamil too: "யோசனை: " (Idea), "கதை: " (Story), "பிரச்சனை: " (Problem), "கேள்வி: " (Question), "ஆபத்து: " (Risk), "தடை: " (Blocker).
  When a row is in English, keep "Idea: ", "Story: ", "Problem: ", "Question: ", "Risk: ", "Blocker: ".

Extract ONLY these four things, each as its own row (use only what is in the transcript):
1. IDEAS: new ideas, improvements, suggestions, creative solutions, product / feature / design / process / business ideas. Topic starts with "Idea: ".
2. DECISIONS: what was finally decided, agreed, approved or confirmed. Type "Decision", and write it in the "decision" field.
3. ACTION ITEMS: a task someone must do. Type "Action Item"; "next_action" is the task, "owner" is who (only if named), "due_date" the deadline (only if said), "priority" only if said.
4. IMPORTANT DISCUSSION POINTS: key problems, important questions raised, risks, blockers or concerns. Type "Information"; start the topic with "Problem: ", "Question: ", "Risk: " or "Blocker: ".
Ignore greetings, small talk, filler words and repeated content. Keep every topic, summary and next action short, clear and actionable.

Rules:
- One row per topic, story or idea that was really discussed. Do not invent topics, owners, dates or decisions that were not said. Leave a field "" if it was not said.
- STORIES MATTER, DO NOT SKIP THEM. People often explain a problem or an idea through a story or an example. Read every story to understand what it means: what happened, what the person learned, and what it leads to for the team's work.
- Every story gets its own row: Topic "Story: <short name>", Type "Information", and a summary of what the story was and its point (1-3 short sentences).
- Then find the IDEA inside the story. Ideas are easy to miss because someone says them suddenly in the middle of a story. Watch for "idea", "what if", "we can", "we could", "why don't we", "let's try", "should we", "maybe we", and also for ideas that are only implied by the story's lesson. Every idea gets its own row: Topic starts with "Idea: ", the summary says the idea in one sentence and what in the story led to it. Type is "Action Item" if someone will try or check it, otherwise "Information".
- Do not invent an idea the story does not contain. If a story has no idea in it, keep only its "Story:" row.
- If the same idea or topic comes up again later in the meeting, put it in one row, not two.
- "type": "Action Item" (someone has to do something), "Information" (just shared), or "Decision" (the team agreed something).
- "priority": "High", "Medium" or "Low" (Medium when unclear).
- "status": "Not Started", "In Progress" or "Completed" (only if it was said; otherwise "").
- "due_date": as dd-Mon-yyyy (for example 08-Dec-2026) only if a date or day was said.
- "summary": 1-3 short sentences. "decision": the agreed outcome. "next_action": the next step.
- If the topic is the same as a row already in the log (listed below), set "update_of" to that row's topic EXACTLY as written; otherwise "update_of": "".

Rows already in the log (topic | status):
{existing}

Return a JSON array. Each item has exactly these keys: {keys}.

Transcript:
{transcript}
"""


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def parse_rows(answer: str) -> list[dict[str, str]]:
    """Claude's JSON answer -> clean rows (anything unusable is dropped)."""
    m = re.search(r"\[.*\]", answer, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    rows = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and str(item.get("topic") or "").strip():
            rows.append({k: str(item.get(k) or "").strip()[:1000] for k in KEYS})
    return rows


async def analyze(claude: Any, options: Any, lines: list[dict[str, Any]], date: str, existing: list[tuple[str, str]]) -> list[dict[str, str]]:
    text = "\n".join(f"{ln['speaker']}: {ln['text']}" for ln in lines)[:MAX_TRANSCRIPT]
    ex = "\n".join(f"{t} | {s}" for t, s in existing[:200]) or "(none)"
    answer = await claude.run_custom(
        PROMPT.format(date=date, existing=ex, keys=", ".join(KEYS), transcript=text), options, timeout=240
    )
    rows = parse_rows(answer)
    if not rows:
        raise ClaudeCodeError("Claude did not find any topics to log in that meeting.")
    return rows


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _a1(row: int, cols: int) -> str:
    return f"A{row}:{_col(cols)}{row}"


def meeting_date(iso: str | None) -> str:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).strftime("%d-%b-%Y")
    except ValueError:
        return datetime.now().strftime("%d-%b-%Y")


class ExcelLog:
    """The Discussion Log sheet of one workbook, reached through a OneDrive/SharePoint share link."""

    def __init__(self, http: httpx.AsyncClient, token: Callable[[], Awaitable[str]], share_url: str) -> None:
        self._http, self._token, self._url = http, token, share_url
        self._base = ""
        self._table = ""
        self._rownum: list[int] = []  # sheet row of each row returned by read() (sheet mode)
        self._next = 0  # first free sheet row after the data (sheet mode)

    async def _call(self, method: str, path: str, what: str, body: Any = None) -> dict[str, Any]:
        try:
            res = await self._http.request(
                method, GRAPH + path, json=body, headers={"Authorization": f"Bearer {await self._token()}"}, follow_redirects=True
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GraphError("Could not reach Microsoft (network error or timeout).", 504) from exc
        if res.status_code >= 400:
            try:
                why = (res.json().get("error") or {}).get("message", "")
            except ValueError:
                why = ""
            logger.warning("Excel %s %s -> HTTP %s %s", method, path.split("?")[0][:80], res.status_code, why)
            if res.status_code == 423:
                raise GraphError("The Excel file is locked right now. Close it where it is open and try again.", 423)
            raise _explain(res.status_code, what, why)
        return res.json() if res.content else {}

    async def open(self) -> None:
        sid = "u!" + base64.urlsafe_b64encode(self._url.encode()).decode().rstrip("=")
        item = await self._call("GET", f"/shares/{sid}/driveItem?$select=id,name,parentReference", "open that Excel file")
        drive = (item.get("parentReference") or {}).get("driveId")
        if not drive or not item.get("id"):
            raise GraphError("That link is not an Excel file you can open.", 400)
        self._base = f"/drives/{drive}/items/{item['id']}/workbook/worksheets('{quote(SHEET)}')"
        tables = (await self._call("GET", f"{self._base}/tables?$select=id", "read the Excel tables")).get("value", [])
        self._table = tables[0]["id"] if tables else ""

    async def read(self) -> tuple[list[str], list[list[Any]]]:
        """(header names, data rows) - data rows are in sheet order, row 2 first."""
        if self._table:
            cols = (await self._call("GET", f"{self._base}/tables('{self._table}')/columns?$select=name", "read the Excel columns")).get("value", [])
            rows = (await self._call("GET", f"{self._base}/tables('{self._table}')/rows", "read the Excel rows")).get("value", [])
            return [c["name"] for c in cols], [r["values"][0] for r in rows]
        used = await self._call("GET", f"{self._base}/usedRange?$select=values", "read the Excel sheet")
        vals = used.get("values") or [[]]
        # remember the REAL sheet row of each filled row, and the first free row right after the last filled one
        # (the sheet may have hundreds of empty formatted rows below the data: those must not push new rows down to row 500+)
        self._rownum = [i + 2 for i, r in enumerate(vals[1:]) if any(str(c).strip() for c in r)]
        self._next = (self._rownum[-1] if self._rownum else 1) + 1
        return [str(h) for h in vals[0]], [r for r in vals[1:] if any(str(c).strip() for c in r)]

    async def write(self, header: list[str], existing: list[list[Any]], rows: list[dict[str, str]], date: str) -> tuple[int, int]:
        """Adds new topics and updates known ones. Returns (rows added, rows updated)."""
        idx = {f: next((i for i, h in enumerate(header) if _norm(h).startswith(start)), -1) for f, start in COLUMNS.items()}
        if idx["topic"] < 0:
            raise GraphError("The sheet has no 'Topic' column in its first row.", 422)
        topics = {_norm(r[idx["topic"]]): n for n, r in enumerate(existing) if idx["topic"] < len(r) and str(r[idx["topic"]]).strip()}
        added = updated = 0
        fresh: list[list[Any]] = []
        if len(self._rownum) != len(existing) or not self._next:  # write() called without read(): assume the rows are contiguous from row 2
            self._rownum = [n + 2 for n in range(len(existing))]
            self._next = len(existing) + 2
        for r in rows:
            n = topics.get(_norm(r["update_of"])) if r["update_of"] else topics.get(_norm(r["topic"]))
            if n is not None:
                cur = list(existing[n]) + [""] * (len(header) - len(existing[n]))
                changed = False
                for f in UPDATABLE:
                    if idx[f] >= 0 and r[f] and str(cur[idx[f]]).strip() != r[f]:
                        cur[idx[f]], changed = r[f], True
                if changed:
                    existing[n] = cur
                    await self._put_row(n, header, cur)
                    updated += 1
                continue
            new = [""] * len(header)
            vals = {**r, "date": date}
            for f, i in idx.items():
                if i >= 0:
                    new[i] = vals.get(f, "")
            existing.append(new)
            topics[_norm(r["topic"])] = len(existing) - 1
            self._rownum.append(self._next + len(fresh))
            fresh.append(new)
            added += 1
        if fresh:
            await self._add_rows(fresh)  # all new rows in ONE call (one call per row was the slow part)
            self._next += len(fresh)
        return added, updated

    async def _put_row(self, n: int, header: list[str], values: list[Any]) -> None:
        if self._table:
            await self._call("PATCH", f"{self._base}/tables('{self._table}')/rows/itemAt(index={n})", "update an Excel row", {"values": [values]})
        else:
            await self._call("PATCH", f"{self._base}/range(address='{_a1(self._rownum[n], len(header))}')", "update an Excel row", {"values": [values]})

    async def _add_rows(self, rows: list[list[Any]]) -> None:
        if self._table:
            await self._call("POST", f"{self._base}/tables('{self._table}')/rows/add", "add Excel rows", {"values": rows})
        else:
            addr = f"A{self._next}:{_col(len(rows[0]))}{self._next + len(rows) - 1}"
            await self._call("PATCH", f"{self._base}/range(address='{addr}')", "add Excel rows", {"values": rows})
