"""Builds the compact, size-limited PR context that is handed to Claude Code."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..azure_devops.models import ChangesResponse, CommentThread, Commit, PullRequestDetail

COMMENT_CHAR_LIMIT = 800
COMMIT_LIMIT = 50


@dataclass
class PRContext:
    pr: PullRequestDetail
    commits: list[Commit]
    changes: ChangesResponse
    threads: list[CommentThread]
    max_files: int
    max_chars_per_file: int
    max_chars_total: int
    max_comments: int
    truncated: bool = False
    files_included: int = 0
    notes: list[str] = field(default_factory=list)
    diff_section: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        pr = self.pr
        out: list[str] = [
            "<pull_request_metadata>",
            f"ID: {pr.pull_request_id}",
            f"Title: {pr.title}",
            f"Status: {pr.status}{' (draft)' if pr.is_draft else ''}",
            f"Author: {pr.author.display_name}",
            f"Source branch: {pr.source_branch}",
            f"Target branch: {pr.target_branch}",
            f"Created: {pr.creation_date}",
            f"Latest iteration: {pr.latest_iteration_id}",
            "Reviewers: " + (", ".join(f"{r.display_name} ({r.vote_label})" for r in pr.reviewers) or "none"),
            "Description:",
            (pr.description or "(none)")[:4000],
            "</pull_request_metadata>",
            "",
            "<commits>",
        ]
        out += [f"- {c.commit_id[:8]} {c.comment.splitlines()[0][:200] if c.comment else ''}" for c in self.commits[:COMMIT_LIMIT]]
        out.append("</commits>")
        out.append("")
        out.append(f"<changed_files total=\"{self.changes.total_files}\" included=\"{self.files_included}\">")
        for f in self.changes.files[: self.max_files]:
            rename = f" (from {f.original_path})" if f.original_path else ""
            out.append(f"- [{f.change_type}] {f.path}{rename} +{f.additions}/-{f.deletions}")
        out.append("</changed_files>")
        out.append("")
        out.append("<unified_diff>")
        out += self.diff_section
        out.append("</unified_diff>")
        if self.threads:
            out += ["", "<existing_review_comments>"]
            for t in self.threads[: self.max_comments]:
                loc = f"{t.file_path}:{t.line}" if t.file_path else "general"
                first = t.comments[0]
                out.append(f"- [{t.status or 'active'}] {loc} {first.author}: {first.content[:COMMENT_CHAR_LIMIT]}")
            out.append("</existing_review_comments>")
        if self.notes:
            out += ["", "<context_notes>", *self.notes, "</context_notes>"]
        return "\n".join(out)

    def render_diffs(self) -> None:
        """Applies the size limits once; populates diff_section, files_included and truncated."""
        out: list[str] = []
        self.files_included = 0
        budget = self.max_chars_total
        for f in self.changes.files[: self.max_files]:
            if not f.diff:
                if f.diff_skipped_reason:
                    out.append(f"### {f.path}: {f.diff_skipped_reason}")
                continue
            diff = f.diff
            if len(diff) > self.max_chars_per_file:
                diff = diff[: self.max_chars_per_file] + "\n... [diff truncated for this file]"
                self.truncated = True
            if budget <= 0:
                out.append(f"### {f.path}: omitted (total diff size limit reached)")
                self.truncated = True
                continue
            if len(diff) > budget:
                diff = diff[:budget] + "\n... [diff truncated: total size limit reached]"
                self.truncated = True
            budget -= len(diff)
            out.append(f"### {f.path}\n{diff}")
            self.files_included += 1
        self.diff_section = out


def build_pr_context(
    pr: PullRequestDetail,
    commits: list[Commit],
    changes: ChangesResponse,
    threads: list[CommentThread],
    *,
    max_files: int,
    max_chars_per_file: int,
    max_chars_total: int,
    max_comments: int,
) -> PRContext:
    ctx = PRContext(
        pr=pr,
        commits=commits,
        changes=changes,
        threads=threads,
        max_files=max_files,
        max_chars_per_file=max_chars_per_file,
        max_chars_total=max_chars_total,
        max_comments=max_comments,
    )
    if changes.total_files > max_files or changes.files_truncated:
        ctx.truncated = True
        ctx.notes.append(
            f"Only the first {min(max_files, len(changes.files))} of {changes.total_files} changed files were included."
        )
    ctx.render_diffs()
    if ctx.truncated:
        ctx.notes.append("Some diffs were truncated; use your tools to read the full files from the local repository.")
    return ctx
