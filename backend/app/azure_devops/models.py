"""Pydantic models for the data this app returns, plus parsers for raw Azure DevOps JSON."""
from __future__ import annotations

from typing import Any

from ..schemas.common import CamelModel


class Organization(CamelModel):
    name: str
    url: str | None = None


class Project(CamelModel):
    id: str
    name: str
    description: str | None = None


class Repository(CamelModel):
    id: str
    name: str
    default_branch: str | None = None
    web_url: str | None = None


class Identity(CamelModel):
    id: str | None = None
    display_name: str
    unique_name: str | None = None
    image_url: str | None = None


class Reviewer(Identity):
    vote: int = 0
    vote_label: str = "No vote"
    is_required: bool = False


class PullRequestSummary(CamelModel):
    pull_request_id: int
    title: str
    status: str
    is_draft: bool = False
    author: Identity
    source_branch: str
    target_branch: str
    creation_date: str | None = None


class PullRequestDetail(PullRequestSummary):
    description: str | None = None
    reviewers: list[Reviewer] = []
    merge_status: str | None = None
    latest_iteration_id: int | None = None
    web_url: str | None = None


class Commit(CamelModel):
    commit_id: str
    comment: str
    author_name: str | None = None
    author_date: str | None = None


class FileChange(CamelModel):
    path: str
    change_type: str
    original_path: str | None = None
    is_binary: bool = False
    additions: int = 0
    deletions: int = 0
    diff: str | None = None
    diff_truncated: bool = False
    diff_skipped_reason: str | None = None


class ChangesResponse(CamelModel):
    iteration_id: int | None
    total_files: int
    files: list[FileChange]
    files_truncated: bool = False


class CommentItem(CamelModel):
    id: int
    author: str
    content: str
    published_date: str | None = None


class CommentThread(CamelModel):
    id: int
    status: str | None = None
    file_path: str | None = None
    line: int | None = None
    comments: list[CommentItem]


VOTE_LABELS = {10: "Approved", 5: "Approved with suggestions", 0: "No vote", -5: "Waiting for author", -10: "Rejected"}


def parse_identity(raw: dict[str, Any] | None) -> Identity:
    raw = raw or {}
    return Identity(
        id=raw.get("id"),
        display_name=raw.get("displayName") or "Unknown",
        unique_name=raw.get("uniqueName"),
        image_url=raw.get("imageUrl"),
    )


def parse_reviewer(raw: dict[str, Any]) -> Reviewer:
    vote = int(raw.get("vote") or 0)
    base = parse_identity(raw)
    return Reviewer(
        **base.model_dump(),
        vote=vote,
        vote_label=VOTE_LABELS.get(vote, str(vote)),
        is_required=bool(raw.get("isRequired")),
    )


def _summary_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return dict(
        pull_request_id=raw["pullRequestId"],
        title=raw.get("title") or "(no title)",
        status=raw.get("status") or "unknown",
        is_draft=bool(raw.get("isDraft")),
        author=parse_identity(raw.get("createdBy")),
        source_branch=raw.get("sourceRefName") or "",
        target_branch=raw.get("targetRefName") or "",
        creation_date=raw.get("creationDate"),
    )


def parse_pr_summary(raw: dict[str, Any]) -> PullRequestSummary:
    return PullRequestSummary(**_summary_fields(raw))


def parse_pr_detail(raw: dict[str, Any], latest_iteration_id: int | None) -> PullRequestDetail:
    web_url = None
    repo_url = (raw.get("repository") or {}).get("webUrl")
    if repo_url:
        web_url = f"{repo_url}/pullrequest/{raw['pullRequestId']}"
    return PullRequestDetail(
        **_summary_fields(raw),
        description=raw.get("description"),
        reviewers=[parse_reviewer(r) for r in raw.get("reviewers") or []],
        merge_status=raw.get("mergeStatus"),
        latest_iteration_id=latest_iteration_id,
        web_url=web_url,
    )


def parse_commit(raw: dict[str, Any]) -> Commit:
    author = raw.get("author") or {}
    return Commit(
        commit_id=raw["commitId"],
        comment=raw.get("comment") or "",
        author_name=author.get("name"),
        author_date=author.get("date"),
    )


def parse_threads(raw_threads: list[dict[str, Any]]) -> list[CommentThread]:
    threads: list[CommentThread] = []
    for t in raw_threads:
        comments = [
            CommentItem(
                id=c["id"],
                author=(c.get("author") or {}).get("displayName") or "Unknown",
                content=c.get("content") or "",
                published_date=c.get("publishedDate"),
            )
            for c in t.get("comments") or []
            if not c.get("isDeleted") and c.get("commentType") != "system"
        ]
        if not comments:
            continue
        ctx = t.get("threadContext") or {}
        right = ctx.get("rightFileStart") or {}
        threads.append(
            CommentThread(
                id=t["id"],
                status=t.get("status"),
                file_path=ctx.get("filePath"),
                line=right.get("line"),
                comments=comments,
            )
        )
    return threads
