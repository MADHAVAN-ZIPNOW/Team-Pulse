from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import get_ado_client, get_current_session, require_same_origin
from ..services.session_store import Session
from .claude_routes import effective_claude_token
from ..services.az_policy import AzContext
from ..azure_devops import models as m
from ..azure_devops.client import AzureDevOpsClient
from ..claude.azure_tools import build_azure_server
from ..claude.claude_code_service import ClaudeCodeError, ClaudeCodeService
from ..config import Settings, get_settings
from ..schemas.ask import AskRequest, AskResponse
from ..services.pr_context import build_pr_context
from ..services.pull_requests import get_pr_changes, latest_iteration
from ..services.rate_limit import RateLimiter, rate_limit_dependency

router = APIRouter(prefix="/api", tags=["chat"])

_ask_limiter = RateLimiter(max_requests=10, window_seconds=60)


def scope_description(body: AskRequest) -> str:
    lines = [
        "<azure_devops_scope>",
        f"Organization: {body.organization}",
        f"Project: {body.project}",
        f"Repository: {body.repository or '(none selected - use list_repositories)'}",
        "Your mcp__azure__* tools default to this project/repository. 'Latest code' means the head of a branch.",
        "</azure_devops_scope>",
    ]
    return "\n".join(lines)


@router.post(
    "/ask",
    response_model=AskResponse,
    response_model_by_alias=True,
    dependencies=[
        Depends(get_current_session),
        Depends(require_same_origin),
        Depends(rate_limit_dependency(_ask_limiter, "ask")),
    ],
)
async def ask(
    body: AskRequest,
    request: Request,
    client: Annotated[AzureDevOpsClient, Depends(get_ado_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_current_session)],
) -> AskResponse:
    claude_token = effective_claude_token(session, settings)
    if settings.require_user_claude_token and not claude_token:
        raise HTTPException(status_code=400, detail="Connect your own Claude account first (paste your Claude token above the chat).")
    org, project, repo, pr_id = body.organization, body.project, body.repository, body.pull_request_id
    context_text = scope_description(body)
    files_analyzed = files_total = 0
    truncated = False

    if pr_id is not None and repo:
        raw_pr, iterations, commits_raw, threads_raw = await asyncio.gather(
            client.get_pull_request(org, project, repo, pr_id),
            client.list_iterations(org, project, repo, pr_id),
            client.list_commits(org, project, repo, pr_id),
            client.list_threads(org, project, repo, pr_id),
        )
        latest = latest_iteration(iterations)
        pr = m.parse_pr_detail(raw_pr, latest["id"] if latest else None)
        changes = await get_pr_changes(client, org, project, repo, pr_id, max_files=settings.max_changed_files)
        ctx = build_pr_context(
            pr,
            [m.parse_commit(c) for c in commits_raw],
            changes,
            m.parse_threads(threads_raw),
            max_files=settings.max_changed_files,
            max_chars_per_file=settings.max_diff_chars_per_file,
            max_chars_total=settings.max_diff_chars_total,
            max_comments=settings.max_comments,
        )
        context_text = f"{context_text}\n\n{ctx.to_prompt()}"
        files_analyzed, files_total, truncated = ctx.files_included, changes.total_files, ctx.truncated

    az = None
    if settings.az_enabled and session.az_dir and session.az_user:  # az_user is set once az sign-in is verified
        az = AzContext(config_dir=session.az_dir, org=org, policy=settings.az_policy)
    server, tool_names = build_azure_server(client, org, project, repo)
    service: ClaudeCodeService = request.app.state.claude
    try:
        answer = await service.ask(
            body.question, context_text, mcp_server=server, azure_tools=tool_names, review_mode=pr_id is not None,
            oauth_token=claude_token,
            az=az,
        )
    except ClaudeCodeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AskResponse(
        answer=answer,
        pull_request_id=pr_id,
        files_analyzed=files_analyzed,
        files_total=files_total,
        context_truncated=truncated,
    )
