"""Read-only Azure DevOps tools exposed to Claude Code as an in-process MCP server.

Every tool runs with the signed-in user's token (via AzureDevOpsClient), is scoped to the selected
organization, and can only read. Results are size-capped.
"""
from __future__ import annotations

import base64
import html
import logging
import re
from typing import Any
from urllib.parse import unquote

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from ..azure_devops import models as m
from ..azure_devops.client import AzureDevOpsClient, AzureDevOpsError
from ..config import get_settings
from ..services.attachments import extract_document_text
from ..services.pull_requests import get_pr_changes, latest_iteration

logger = logging.getLogger("pr_assistant.tools")

SERVER_NAME = "azure"
MAX_TEXT_CHARS = 40_000
MAX_FILE_CHARS = 60_000
MAX_LIST = 300
MAX_IMAGE_BYTES = 4_500_000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
PR_ATTACH_RE = re.compile(r"pullRequests/\d+/attachments/([^)\s\"'<>]+)", re.IGNORECASE)
GUID_RE = re.compile(r"/_apis/wit/attachments/([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})")
IMG_SRC_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
WORK_ITEM_FIELDS = [
    "System.Id", "System.WorkItemType", "System.Title", "System.State", "System.AssignedTo",
    "System.ChangedDate", "System.TeamProject", "System.Tags", "System.IterationPath",
]


def _text(value: str, error: bool = False, limit: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": value[:limit]}]}
    if error:
        result["is_error"] = True
    return result


_RISKY = re.compile(
    r"auth|login|token|secret|password|crypt|security|permission|valid|citation|sql|migrat|schema|model|config|env|"
    r"docker|deploy|workflow|pipeline|payment|upload|api|route|service|pipeline",
    re.IGNORECASE,
)
_LOW_VALUE = re.compile(r"(^|/)(tests?|docs?|assets|static)/|\.(md|txt|lock|svg|png|jpg|json|csv|snap)$|test_|\.test\.", re.IGNORECASE)


def _risk_score(path: str) -> int:
    """Rough 'look at this first' score from the path alone: security/data/config code high, tests/docs/assets low."""
    score = len(_RISKY.findall(path))
    return score - 3 if _LOW_VALUE.search(path) else score


def _coverage(state: dict[str, Any]) -> str:
    """Tells Claude exactly how much of the PR it has read, so it cannot stop early without noticing."""
    total, done = len(state["order"]), len(state["read"])
    waiting = [p for p in state["order"] if p not in state["claimed"]]
    lines = [f"COVERAGE: read {done} of {total} changed files."]
    if waiting:
        lines.append(
            f"{len(waiting)} files are still UNREAD. Do NOT write your answer yet: call get_pr_file_diff with paths=NEXT "
            f"again (several calls in parallel) until this line says ALL FILES READ."
        )
    elif done < total:
        lines.append("Other batches are still loading; wait for them, then check again with paths=NEXT.")
    else:
        lines.append("ALL FILES READ.")
    if state["notes"]:
        lines.append("Needs a closer look (cut off or not shown): " + "; ".join(f"{p} [{n}]" for p, n in list(state["notes"].items())[:20]))
    return "\n".join(lines)


def _schema(props: dict[str, tuple[str, str]], required: list[str] | None = None) -> dict[str, Any]:
    """props: name -> (json type, description)."""
    return {
        "type": "object",
        "properties": {k: {"type": t, "description": d} for k, (t, d) in props.items()},
        "required": required or [],
    }


def _strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<(br|/p|/div|/li|/tr)\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _image_result(data: bytes, label: str) -> dict[str, Any]:
    mime = _sniff_image(data)
    if mime is None:
        return _text(f"{label}: not a supported image (png/jpg/gif/webp).", True)
    if len(data) > MAX_IMAGE_BYTES:
        return _text(f"{label}: image is too large ({len(data) // 1024} KB).", True)
    return {
        "content": [
            {"type": "text", "text": f"Image: {label}"},
            {"type": "image", "data": base64.b64encode(data).decode(), "mimeType": mime},
        ]
    }


def _wiql_quote(value: str) -> str:
    return value.replace("'", "''")


def build_tools(
    client: AzureDevOpsClient,
    organization: str,
    default_project: str,
    default_repository: str | None,
) -> list[Any]:
    settings = get_settings()
    pr_reads: dict[int, dict[str, Any]] = {}  # per PR: which changed files Claude has read so far

    def project_of(args: dict[str, Any]) -> str:
        project = (args.get("project") or default_project or "").strip()
        if not project:
            raise AzureDevOpsError("No project selected. Pass the 'project' argument (see list_projects).", 400)
        return project

    def repo_of(args: dict[str, Any]) -> str:
        repo = (args.get("repository") or default_repository or "").strip()
        if not repo:
            raise AzureDevOpsError("No repository selected. Pass the 'repository' argument (see list_repositories).", 400)
        return repo

    def guarded(fn):  # type: ignore[no-untyped-def]
        async def wrapper(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return await fn(args or {})
            except AzureDevOpsError as exc:
                return _text(f"Azure DevOps error: {exc.message}", True)
            except Exception:  # noqa: BLE001
                logger.exception("Tool %s failed", getattr(fn, "__name__", "?"))
                return _text("The tool failed unexpectedly.", True)

        wrapper.__name__ = fn.__name__
        return wrapper

    PROJ = ("string", "Project name (defaults to the selected project)")
    REPO = ("string", "Repository name (defaults to the selected repository)")
    BRANCH = ("string", "Branch name without refs/heads/ (defaults to the repository's default branch)")

    @tool("list_projects", "List all projects in the Azure DevOps organization.", _schema({}))
    @guarded
    async def list_projects(args: dict[str, Any]) -> dict[str, Any]:
        projects = await client.list_projects(organization)
        return _text(chr(10).join(p.get("name", "") for p in projects) or "No projects found.")

    @tool("list_repositories", "List the Git repositories in a project.", _schema({"project": PROJ}))
    @guarded
    async def list_repositories(args: dict[str, Any]) -> dict[str, Any]:
        repos = await client.list_repositories(organization, project_of(args))
        lines = [f"- {r['name']} (default branch: {(r.get('defaultBranch') or '?').replace('refs/heads/', '')})" for r in repos]
        return _text("\n".join(lines) or "No repositories.")

    @tool("list_branches", "List branches of a repository.", _schema({"project": PROJ, "repository": REPO}))
    @guarded
    async def list_branches(args: dict[str, Any]) -> dict[str, Any]:
        refs = await client.list_branches(organization, project_of(args), repo_of(args))
        return _text("\n".join(r["name"].replace("refs/heads/", "") for r in refs) or "No branches.")

    @tool(
        "list_files",
        "List files and folders in a repository at the LATEST commit of a branch. Use recursive=true for the full tree.",
        _schema(
            {"project": PROJ, "repository": REPO, "branch": BRANCH, "path": ("string", "Folder path, default '/'"),
             "recursive": ("boolean", "List the whole subtree (capped at 300 entries)")}
        ),
    )
    @guarded
    async def list_files(args: dict[str, Any]) -> dict[str, Any]:
        items = await client.list_items(
            organization, project_of(args), repo_of(args), args.get("path") or "/", args.get("branch"), bool(args.get("recursive"))
        )
        lines = [("[dir]  " if i.get("isFolder") else "[file] ") + i["path"] for i in items[:MAX_LIST]]
        if len(items) > MAX_LIST:
            lines.append(f"... {len(items) - MAX_LIST} more entries not shown; narrow the path.")
        return _text("\n".join(lines) or "Nothing found at that path.")

    @tool(
        "get_file",
        "Read a file from a repository at the LATEST commit of a branch. Image files (png/jpg/gif/webp) are returned as images.",
        _schema({"project": PROJ, "repository": REPO, "branch": BRANCH, "path": ("string", "File path, e.g. /src/app.py")}, ["path"]),
    )
    @guarded
    async def get_file(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path") or "")
        data = await client.get_file_bytes(organization, project_of(args), repo_of(args), path, args.get("branch"))
        if path.lower().endswith(IMAGE_EXT):
            return _image_result(data, path)
        if b"\x00" in data[:8000]:
            return _text(f"{path} is a binary file ({len(data)} bytes).", True)
        body = data.decode("utf-8", errors="replace")
        note = f"\n... [truncated, {len(body)} chars total]" if len(body) > MAX_FILE_CHARS else ""
        return _text(f"# {path}\n{body[:MAX_FILE_CHARS]}{note}")

    @tool(
        "list_pull_requests",
        "List pull requests of a repository (status: active, completed, abandoned or all).",
        _schema({"project": PROJ, "repository": REPO, "status": ("string", "active (default), completed, abandoned, all")}),
    )
    @guarded
    async def list_pull_requests(args: dict[str, Any]) -> dict[str, Any]:
        status = args.get("status") if args.get("status") in ("active", "completed", "abandoned", "all") else "active"
        prs = await client.list_pull_requests(organization, project_of(args), repo_of(args), status=status, max_items=50)
        lines = [
            f"#{p['pullRequestId']} [{p.get('status')}] {p.get('title')} - {(p.get('createdBy') or {}).get('displayName')} "
            f"({(p.get('sourceRefName') or '').replace('refs/heads/', '')} -> {(p.get('targetRefName') or '').replace('refs/heads/', '')})"
            for p in prs
        ]
        return _text("\n".join(lines) or "No pull requests.")

    @tool(
        "get_pull_request",
        "FAST overview of a pull request: metadata, commits, review comments and the list of ALL changed files "
        "(riskiest first, with +added/-removed line counts), plus whether the source branch has moved since the PR. "
        "It does NOT include diffs: read them with get_pr_file_diff.",
        _schema({"project": PROJ, "repository": REPO, "pull_request_id": ("integer", "Pull request ID")}, ["pull_request_id"]),
    )
    @guarded
    async def get_pull_request(args: dict[str, Any]) -> dict[str, Any]:
        project, repo, pr_id = project_of(args), repo_of(args), int(args["pull_request_id"])
        raw, iterations, commits, threads, branches, attachments, linked = await _gather(
            client.get_pull_request(organization, project, repo, pr_id),
            client.list_iterations(organization, project, repo, pr_id),
            client.list_commits(organization, project, repo, pr_id),
            client.list_threads(organization, project, repo, pr_id),
            client.list_branches(organization, project, repo),
            _tolerant(client.list_pr_attachments(organization, project, repo, pr_id)),
            _tolerant(client.list_pr_work_items(organization, project, repo, pr_id)),
        )
        text_for_links = (raw.get("description") or "") + " " + " ".join(
            (c.get("content") or "") for t in threads for c in (t.get("comments") or [])
        )
        names = {a.get("displayName") for a in attachments if a.get("displayName")}
        names |= {unquote(n) for n in PR_ATTACH_RE.findall(text_for_links)}
        latest = latest_iteration(iterations)
        entries = await client.get_iteration_changes(organization, project, repo, pr_id, latest["id"]) if latest else []
        files = [
            (e["item"]["path"], str(e.get("changeType") or "edit"))
            for e in entries
            if (e.get("item") or {}).get("path") and not (e.get("item") or {}).get("isFolder")
        ]
        files.sort(key=lambda f: (-_risk_score(f[0]), f[0]))
        pr_reads[pr_id] = {"order": [p for p, _ in files], "claimed": set(), "read": set(), "notes": {}}
        src = (raw.get("sourceRefName") or "")
        pr_commit = ((latest or {}).get("sourceRefCommit") or {}).get("commitId") or ""
        head = next((b.get("objectId") for b in branches if b.get("name") == src), "")
        if head and pr_commit:
            branch_note = "same as the PR (nothing newer)" if head == pr_commit else f"NEWER than the PR (branch head {head[:8]}, PR {pr_commit[:8]}): review get_file on the branch too"
        else:
            branch_note = "unknown"
        lines = [
            f"PR #{pr_id}: {raw.get('title')}",
            f"Status: {raw.get('status')}{' (draft)' if raw.get('isDraft') else ''}  Author: {(raw.get('createdBy') or {}).get('displayName')}",
            f"{src.replace('refs/heads/', '')} -> {(raw.get('targetRefName') or '').replace('refs/heads/', '')}",
            f"Source branch vs PR: {branch_note}",
            f"Description: {_strip_html(raw.get('description'))[:8000] or '(none)'}",
            "",
            f"Branch to compare: {src.replace('refs/heads/', '')} (the PR's source branch) into "
            f"{(raw.get('targetRefName') or '').replace('refs/heads/', '')}",
            f"PR images/attachments ({len(names)}): " + (", ".join(sorted(names)) or "none")
            + ("  <- open EVERY one with get_pr_attachment" if names else ""),
            f"Linked work items: " + (", ".join(str(w.get("id")) for w in linked) or "none")
            + ("  <- read them with get_work_item (also open their images)" if linked else ""),
            "",
            f"Commits ({len(commits)}):",
            *[f"- {c.get('commitId', '')[:8]} {(c.get('comment') or '').splitlines()[0][:150] if c.get('comment') else ''}" for c in commits[:40]],
            "",
            f"Changed files ({len(files)}), riskiest first. You must read ALL of them: call get_pr_file_diff with paths=NEXT, "
            f"{(len(files) + 7) // 8} times in total (several in parallel). Its COVERAGE line tells you when you are done.",
            *[f"- [{ct}] {p}" for p, ct in files[:400]],
        ]
        if len(files) > 400:
            lines.append(f"... {len(files) - 400} more files not listed.")
        human = [
            t for t in threads
            if t.get("comments") and (t["comments"][0].get("commentType") != "system") and not t.get("isDeleted")
        ]
        if human:
            lines += ["", f"Review comments ({len(human)} threads):"]
            for t in human[:25]:
                c = t["comments"][0]
                where = (t.get("threadContext") or {}).get("filePath") or ""
                lines.append(f"- [{t.get('status')}] {where} {(c.get('author') or {}).get('displayName')}: {(c.get('content') or '')[:300]}")
        return _text("\n".join(lines))

    @tool(
        "get_pr_file_diff",
        "Unified diff (what changed) for files of a pull request. Pass paths=\"NEXT\" to get the next 8 UNREAD files "
        "(riskiest first); you can issue several NEXT calls in parallel, each gets a different batch. Or pass up to 8 "
        "comma-separated paths. Every result ends with a COVERAGE line: keep calling until it says ALL FILES READ.",
        _schema(
            {
                "project": PROJ, "repository": REPO, "pull_request_id": ("integer", "Pull request ID"),
                "paths": ("string", "\"NEXT\" (recommended) or comma-separated file paths exactly as listed, max 8"),
            },
            ["pull_request_id", "paths"],
        ),
    )
    @guarded
    async def get_pr_file_diff(args: dict[str, Any]) -> dict[str, Any]:
        project, repo, pr_id = project_of(args), repo_of(args), int(args["pull_request_id"])
        state = pr_reads.get(pr_id)
        raw_paths = str(args.get("paths") or "").strip()
        if raw_paths.upper() == "NEXT":
            if state is None:
                return _text("Call get_pull_request for this PR first, then use paths=NEXT.", True)
            wanted = [p for p in state["order"] if p not in state["claimed"]][:8]  # claimed at once: parallel calls get different files
            state["claimed"].update(wanted)
            if not wanted:
                return _text(_coverage(state))
        else:
            wanted = [p.strip() for p in raw_paths.split(",") if p.strip()][:8]
            if not wanted:
                return _text("Give paths=NEXT or at least one path.", True)
            if state is not None:
                state["claimed"].update(wanted)
        try:
            changes = await get_pr_changes(client, organization, project, repo, pr_id, max_files=8, only_paths=set(wanted))
        except Exception:
            if state is not None:
                state["claimed"].difference_update(wanted)  # a failed batch must be offered again, not counted as read
            raise
        if not changes.files:
            return _text("None of those paths are in this PR. Use paths=NEXT, or the exact paths from get_pull_request.", True)
        per_file = min(20_000, 80_000 // len(changes.files))  # fewer files per call = more of each file
        parts = []
        for f in changes.files:
            if state is not None:
                state["read"].add(f.path)
            if f.diff_skipped_reason:
                if state is not None:
                    state["notes"][f.path] = f"diff not shown ({f.diff_skipped_reason})"
                parts.append(f"### {f.path} [{f.change_type}]\n(diff not shown: {f.diff_skipped_reason})")
                continue
            diff = f.diff or "(no textual change)"
            cut = len(diff) > per_file
            if state is not None:
                if cut and len(changes.files) > 1:
                    state["notes"][f.path] = f"cut at {per_file} of {len(diff)} chars: call get_pr_file_diff with just this path"
                elif cut:
                    state["notes"][f.path] = f"cut at {per_file} of {len(diff)} chars: read the rest with get_file on the branch"
                else:
                    state["notes"].pop(f.path, None)
            note = f"\n... [diff cut: {len(diff)} chars, showing first {per_file}; call again with just this path, or use get_file on the branch for the rest]" if cut else ""
            parts.append(f"### {f.path} [{f.change_type}] +{f.additions}/-{f.deletions}\n{diff[:per_file]}{note}")
        if state is not None:
            parts.append(_coverage(state))
        return _text("\n\n".join(parts), limit=87_000)

    @tool(
        "search_work_items",
        "Find work items (tasks, bugs, user stories...) in the selected project. Provide filters, or a full WIQL query "
        "(read-only). Returns id, type, state, title, assignee. Use get_work_item for details and images.",
        _schema(
            {
                "state": ("string", "e.g. Active, New, Closed. Omit for all non-closed items"),
                "work_item_type": ("string", "e.g. Task, Bug, User Story"),
                "assigned_to": ("string", "Display name/email, or @Me"),
                "text": ("string", "Words contained in the title"),
                "wiql": ("string", "Advanced: a full WIQL SELECT query (overrides other filters)"),
                "top": ("integer", "Max results (default 30, max 100)"),
            }
        ),
    )
    @guarded
    async def search_work_items(args: dict[str, Any]) -> dict[str, Any]:
        project = project_of(args)
        top = max(1, min(int(args.get("top") or 30), 100))
        wiql = (args.get("wiql") or "").strip()
        if wiql:
            if not wiql.upper().startswith("SELECT"):
                return _text("wiql must be a SELECT query.", True)
        else:
            clauses = ["[System.TeamProject] = @project"]
            if args.get("state"):
                clauses.append(f"[System.State] = '{_wiql_quote(str(args['state']))}'")
            else:
                clauses.append("[System.State] NOT IN ('Closed', 'Done', 'Removed')")
            if args.get("work_item_type"):
                clauses.append(f"[System.WorkItemType] = '{_wiql_quote(str(args['work_item_type']))}'")
            if args.get("assigned_to"):
                who = str(args["assigned_to"])
                clauses.append("[System.AssignedTo] = @Me" if who == "@Me" else f"[System.AssignedTo] CONTAINS '{_wiql_quote(who)}'")
            if args.get("text"):
                clauses.append(f"[System.Title] CONTAINS '{_wiql_quote(str(args['text']))}'")
            wiql = "SELECT [System.Id] FROM WorkItems WHERE " + " AND ".join(clauses) + " ORDER BY [System.ChangedDate] DESC"
        ids = await client.query_work_item_ids(organization, project, wiql, top)
        items = await client.get_work_items(organization, ids, WORK_ITEM_FIELDS)
        lines = []
        for w in items:
            f = w.get("fields") or {}
            who = (f.get("System.AssignedTo") or {}).get("displayName", "unassigned") if isinstance(f.get("System.AssignedTo"), dict) else "unassigned"
            lines.append(f"#{w['id']} [{f.get('System.WorkItemType')}/{f.get('System.State')}] {f.get('System.Title')} - {who}")
        return _text("\n".join(lines) or "No matching work items.")

    @tool(
        "get_work_item",
        "Get one work item in full: description, acceptance criteria, repro steps, comments, links (PRs, commits, "
        "related items) and its images/attachments. Use get_attachment to open an image or document.",
        _schema({"work_item_id": ("integer", "Work item ID")}, ["work_item_id"]),
    )
    @guarded
    async def get_work_item(args: dict[str, Any]) -> dict[str, Any]:
        wid = int(args["work_item_id"])
        wi = await client.get_work_item(organization, wid)
        f = wi.get("fields") or {}
        project = f.get("System.TeamProject") or default_project
        assigned = f.get("System.AssignedTo")
        html_fields = {
            "Description": f.get("System.Description"),
            "Acceptance criteria": f.get("Microsoft.VSTS.Common.AcceptanceCriteria"),
            "Repro steps": f.get("Microsoft.VSTS.TCM.ReproSteps"),
        }
        out = [
            f"#{wid} [{f.get('System.WorkItemType')}] {f.get('System.Title')}",
            f"State: {f.get('System.State')} | Assigned to: {assigned.get('displayName') if isinstance(assigned, dict) else 'unassigned'}",
            f"Project: {project} | Iteration: {f.get('System.IterationPath')} | Tags: {f.get('System.Tags') or '-'}",
        ]
        images: dict[str, str] = {}
        for label, raw in html_fields.items():
            if raw:
                out.append(f"\n## {label}\n{_strip_html(raw)}")
                for src in IMG_SRC_RE.findall(raw):
                    if g := GUID_RE.search(src):
                        images[g.group(1)] = "embedded in " + label.lower()
        try:
            for c in await client.list_work_item_comments(organization, project, wid):
                text = c.get("text") or ""
                out.append(f"\n## Comment by {(c.get('createdBy') or {}).get('displayName')}\n{_strip_html(text)}")
                for src in IMG_SRC_RE.findall(text):
                    if g := GUID_RE.search(src):
                        images[g.group(1)] = "embedded in a comment"
        except AzureDevOpsError:
            out.append("\n(comments unavailable)")
        links = []
        for r in wi.get("relations") or []:
            url, name = r.get("url", ""), (r.get("attributes") or {}).get("name")
            if r.get("rel") == "AttachedFile" and (g := GUID_RE.search(url)):
                images[g.group(1)] = f"attachment {name or ''}".strip()
            else:
                links.append(f"- {r.get('rel')}: {(name or url)}")
        if links:
            out.append("\n## Links\n" + "\n".join(links[:40]))
        if images:
            out.append("\n## Images / attachments (open with get_attachment)\n" + "\n".join(f"- attachment_id={k} ({v})" for k, v in images.items()))
        return _text("\n".join(out))

    @tool(
        "get_attachment",
        "Open a work-item attachment or an image embedded in a description/comment. Images are shown to you as "
        "images; Word, PowerPoint, Excel, PDF and text files are returned as extracted text. Pass attachment_id "
        "(preferred) or the attachment URL.",
        _schema({"attachment_id": ("string", "Attachment GUID"), "url": ("string", "Attachment URL from a work item")}),
    )
    @guarded
    async def get_attachment(args: dict[str, Any]) -> dict[str, Any]:
        raw = str(args.get("attachment_id") or args.get("url") or "")
        found = GUID_RE.search(raw) or re.fullmatch(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27})", raw.strip())
        if not found:
            return _text("Could not find an attachment id in the input.", True)
        guid = found.group(1)
        data = await client.get_attachment(organization, guid)
        if _sniff_image(data):
            return _image_result(data, f"attachment {guid}")
        try:
            doc = extract_document_text(data)
        except Exception:  # noqa: BLE001 - corrupt/encrypted documents
            doc = None
        if doc is None:
            return _text(f"Attachment {guid} ({len(data) // 1024} KB) is in a format that cannot be read as text.", True)
        kind, text = doc
        return _text(f"Attachment {guid} ({kind}):\n{text}", limit=80_000)

    @tool(
        "get_pr_attachment",
        "Open an image or document attached to a pull request (in its description or comments). Images are shown to you; "
        "Word/PowerPoint/Excel/PDF/text are returned as text. Pass the file name listed by get_pull_request.",
        _schema(
            {"project": PROJ, "repository": REPO, "pull_request_id": ("integer", "Pull request ID"),
             "file_name": ("string", "Attachment file name, e.g. image.png")},
            ["pull_request_id", "file_name"],
        ),
    )
    @guarded
    async def get_pr_attachment(args: dict[str, Any]) -> dict[str, Any]:
        project, repo, pr_id = project_of(args), repo_of(args), int(args["pull_request_id"])
        name = str(args.get("file_name") or "").strip()
        if not name:
            return _text("Give the attachment file name.", True)
        data = await client.get_pr_attachment(organization, project, repo, pr_id, name)
        if _sniff_image(data):
            return _image_result(data, name)
        try:
            doc = extract_document_text(data)
        except Exception:  # noqa: BLE001
            doc = None
        if doc is None:
            return _text(f"{name} ({len(data) // 1024} KB) cannot be read as text.", True)
        return _text(f"{name} ({doc[0]}):" + chr(10) + doc[1], limit=80_000)

    tools = [
        list_projects, list_repositories, list_branches, list_files, get_file, list_pull_requests,
        get_pull_request, get_pr_file_diff, get_pr_attachment, search_work_items, get_work_item, get_attachment,
    ]
    return tools


def build_azure_server(
    client: AzureDevOpsClient,
    organization: str,
    default_project: str,
    default_repository: str | None,
) -> tuple[McpSdkServerConfig, list[str]]:
    tools = build_tools(client, organization, default_project, default_repository)
    server = create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=tools)
    return server, [f"mcp__{SERVER_NAME}__{t.name}" for t in tools]


async def _tolerant(coro):  # type: ignore[no-untyped-def]
    """Optional extras (attachments, linked items) must never break the main answer."""
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return []


async def _gather(*coros):  # type: ignore[no-untyped-def]
    import asyncio

    return await asyncio.gather(*coros)
