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

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from ..azure_devops import models as m
from ..azure_devops.client import AzureDevOpsClient, AzureDevOpsError
from ..config import get_settings
from ..services.attachments import extract_document_text
from ..services.pr_context import build_pr_context
from ..services.pull_requests import get_pr_changes, latest_iteration

logger = logging.getLogger("pr_assistant.tools")

SERVER_NAME = "azure"
MAX_TEXT_CHARS = 40_000
MAX_FILE_CHARS = 60_000
MAX_LIST = 300
MAX_IMAGE_BYTES = 4_500_000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
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

    def project_of(args: dict[str, Any]) -> str:
        return (args.get("project") or default_project).strip()

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
        "Get a pull request's metadata, commits, changed files, unified diff and review comments.",
        _schema(
            {
                "project": PROJ, "repository": REPO, "pull_request_id": ("integer", "Pull request ID"),
                "offset": ("integer", "Large PRs come in pages of 60000 chars; pass the offset the previous page gave"),
            },
            ["pull_request_id"],
        ),
    )
    @guarded
    async def get_pull_request(args: dict[str, Any]) -> dict[str, Any]:
        project, repo, pr_id = project_of(args), repo_of(args), int(args["pull_request_id"])
        raw, iterations, commits, threads = await _gather(
            client.get_pull_request(organization, project, repo, pr_id),
            client.list_iterations(organization, project, repo, pr_id),
            client.list_commits(organization, project, repo, pr_id),
            client.list_threads(organization, project, repo, pr_id),
        )
        latest = latest_iteration(iterations)
        pr = m.parse_pr_detail(raw, latest["id"] if latest else None)
        changes = await get_pr_changes(client, organization, project, repo, pr_id, max_files=settings.max_changed_files)
        ctx = build_pr_context(
            pr, [m.parse_commit(c) for c in commits], changes, m.parse_threads(threads),
            max_files=settings.max_changed_files, max_chars_per_file=settings.max_diff_chars_per_file,
            max_chars_total=settings.max_diff_chars_total, max_comments=settings.max_comments,
        )
        # Pages of 60k chars stay under Claude Code's tool-output limit (bigger results get saved to a file instead).
        full, offset, page = ctx.to_prompt(), max(int(args.get("offset") or 0), 0), 60_000
        piece = full[offset : offset + page]
        if offset + page < len(full):
            piece += f"\n\n[... PAGE END. {len(full) - offset - page} more chars: call get_pull_request again with offset={offset + page}]"
        return _text(piece, limit=page + 300)

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

    tools = [
        list_repositories, list_branches, list_files, get_file, list_pull_requests,
        get_pull_request, search_work_items, get_work_item, get_attachment,
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


async def _gather(*coros):  # type: ignore[no-untyped-def]
    import asyncio

    return await asyncio.gather(*coros)
