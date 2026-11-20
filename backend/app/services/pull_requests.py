"""Higher-level PR operations: latest iteration, changed files, and locally computed unified diffs.

Azure DevOps has no "unified diff of a PR" endpoint, so we fetch each file at the iteration's
base commit and source commit and diff them here with difflib.
"""
from __future__ import annotations

import asyncio
import difflib
from typing import Any

from ..azure_devops.client import AzureDevOpsClient, AzureDevOpsError
from ..azure_devops.models import ChangesResponse, FileChange

DIFF_CONCURRENCY = 6
MAX_DIFF_FILES = 60  # hard ceiling on files for which we fetch contents/diffs


def latest_iteration(iterations: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(iterations, key=lambda i: i.get("id", 0)) if iterations else None


def unified_diff(old: str, new: str, old_path: str, new_path: str) -> tuple[str, int, int]:
    """Returns (diff_text, additions, deletions)."""
    lines = list(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), fromfile=f"a{old_path}", tofile=f"b{new_path}", lineterm="", n=3
        )
    )
    additions = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return "\n".join(lines), additions, deletions


def _change_kind(change_type: str) -> tuple[bool, bool]:
    ct = (change_type or "").lower()
    return "add" in ct, "delete" in ct


async def get_pr_changes(
    client: AzureDevOpsClient,
    organization: str,
    project: str,
    repository: str,
    pr_id: int,
    max_files: int = MAX_DIFF_FILES,
    with_diffs: bool = True,
) -> ChangesResponse:
    iterations = await client.list_iterations(organization, project, repository, pr_id)
    latest = latest_iteration(iterations)
    if latest is None:
        return ChangesResponse(iteration_id=None, total_files=0, files=[])
    iteration_id = latest["id"]
    source_commit = (latest.get("sourceRefCommit") or {}).get("commitId")
    base_commit = (latest.get("commonRefCommit") or {}).get("commitId")

    entries = await client.get_iteration_changes(organization, project, repository, pr_id, iteration_id)
    entries = [e for e in entries if (e.get("item") or {}).get("path") and not (e.get("item") or {}).get("isFolder")]
    total = len(entries)
    selected = entries[:max_files]

    sem = asyncio.Semaphore(DIFF_CONCURRENCY)

    async def build(entry: dict[str, Any]) -> FileChange:
        path = entry["item"]["path"]
        change_type = str(entry.get("changeType") or "edit")
        original = entry.get("originalPath") or path
        fc = FileChange(path=path, change_type=change_type, original_path=entry.get("originalPath"))
        if not with_diffs or not source_commit:
            return fc
        is_add, is_delete = _change_kind(change_type)
        async with sem:
            try:
                old = None if is_add or not base_commit else await client.get_file_text(
                    organization, project, repository, original, base_commit
                )
                new = None if is_delete else await client.get_file_text(
                    organization, project, repository, path, source_commit
                )
            except AzureDevOpsError as exc:
                fc.diff_skipped_reason = exc.message
                return fc
        for fetched in (old, new):
            if fetched is not None and fetched.skipped:
                fc.is_binary = True
                fc.diff_skipped_reason = "Binary or very large file; diff not shown."
                return fc
        old_text = old.text if old and old.text is not None else ""
        new_text = new.text if new and new.text is not None else ""
        fc.diff, fc.additions, fc.deletions = unified_diff(old_text, new_text, original, path)
        return fc

    files = await asyncio.gather(*(build(e) for e in selected))
    return ChangesResponse(
        iteration_id=iteration_id,
        total_files=total,
        files=list(files),
        files_truncated=total > len(selected),
    )
