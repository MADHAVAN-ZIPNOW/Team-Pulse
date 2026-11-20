"""Async Azure DevOps REST client that authenticates as the signed-in user (Bearer token)."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

logger = logging.getLogger("pr_assistant.ado")

ADO_HOST = "https://dev.azure.com"
VSSPS_HOST = "https://app.vssps.visualstudio.com"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 0.5
PAGE_SIZE = 100
MAX_PAGES = 50
MAX_FILE_BYTES = 1_000_000
NUL = bytes([0])

TokenProvider = Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class FileContent:
    text: str | None
    skipped: bool = False


class AzureDevOpsError(Exception):
    """Carries a user-safe message and the HTTP status to return."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def seg(value: str) -> str:
    """Percent-encode one URL path segment (slashes included)."""
    return quote(str(value), safe="")


def build_url(organization: str, *segments: str, project: str | None = None, host: str = ADO_HOST) -> str:
    """Builds https://dev.azure.com/{org}[/{project}]/_apis/{segments...} with every part encoded."""
    parts = [host, seg(organization)]
    if project:
        parts.append(seg(project))
    parts.append("_apis")
    parts.extend(seg(s) for s in segments)
    return "/".join(parts)


def _friendly_error(status: int) -> AzureDevOpsError:
    if status in (401, 203, 302):
        return AzureDevOpsError("Azure DevOps rejected your sign-in. Sign in again.", 401)
    if status == 403:
        return AzureDevOpsError("You do not have permission to access this Azure DevOps resource.", 403)
    if status == 404:
        return AzureDevOpsError("Azure DevOps resource not found. Check the organization, project, repository and PR.", 404)
    if status == 429:
        return AzureDevOpsError("Azure DevOps is rate limiting requests. Try again shortly.", 429)
    return AzureDevOpsError(f"Azure DevOps request failed (HTTP {status}).", 502)


class AzureDevOpsClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token_provider: TokenProvider,
        api_version: str = "7.1",
        max_retries: int = MAX_RETRIES,
        backoff_base: float = BACKOFF_BASE_SECONDS,
    ) -> None:
        self._http = http
        self._token_provider = token_provider
        self._api_version = api_version
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    # ---- low-level ---------------------------------------------------------------------------
    async def _request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
        json_body: Any | None = None,
    ) -> httpx.Response:
        """GET, or POST when json_body is given (used only for read-only queries such as WIQL)."""
        query = {"api-version": self._api_version, **(params or {})}
        for attempt in range(self._max_retries + 1):
            token = await self._token_provider()
            headers = {"Authorization": f"Bearer {token}", "Accept": accept}
            try:
                if json_body is None:
                    resp = await self._http.get(url, params=query, headers=headers, follow_redirects=False)
                else:
                    resp = await self._http.post(
                        url, params=query, headers=headers, json=json_body, follow_redirects=False
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._max_retries:
                    raise AzureDevOpsError("Could not reach Azure DevOps (network error or timeout).", 504) from exc
                await self._sleep(attempt)
                continue
            if resp.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                await self._sleep(attempt, resp.headers.get("Retry-After"))
                continue
            if resp.status_code in (203, 302) or resp.status_code >= 400:
                logger.warning("ADO %s -> HTTP %s", url.split("?")[0], resp.status_code)
                raise _friendly_error(resp.status_code)
            return resp
        raise AzureDevOpsError("Azure DevOps request failed.", 502)  # pragma: no cover

    async def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self._backoff_base * (2**attempt) + random.uniform(0, self._backoff_base)
        if retry_after and retry_after.isdigit():
            delay = max(delay, min(int(retry_after), 30))
        await asyncio.sleep(delay)

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._request(url, params)
        try:
            return resp.json()
        except ValueError as exc:
            raise AzureDevOpsError("Azure DevOps returned an unreadable response.", 502) from exc

    async def _get_paged(
        self, url: str, params: dict[str, Any] | None = None, key: str = "value", max_items: int | None = None
    ) -> list[dict[str, Any]]:
        """$top/$skip pagination."""
        items: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            data = await self._get_json(url, {**(params or {}), "$top": PAGE_SIZE, "$skip": page * PAGE_SIZE})
            batch = data.get(key) or []
            items.extend(batch)
            if len(batch) < PAGE_SIZE or (max_items is not None and len(items) >= max_items):
                break
        return items[:max_items] if max_items is not None else items

    # ---- organizations / projects / repos ------------------------------------------------------
    async def list_organizations(self) -> list[dict[str, Any]]:
        profile = await self._get_json(f"{VSSPS_HOST}/_apis/profile/profiles/me")
        data = await self._get_json(f"{VSSPS_HOST}/_apis/accounts", {"memberId": profile["id"]})
        return data.get("value") or []

    async def list_projects(self, organization: str) -> list[dict[str, Any]]:
        return await self._get_paged(build_url(organization, "projects"))

    async def list_repositories(self, organization: str, project: str) -> list[dict[str, Any]]:
        data = await self._get_json(build_url(organization, "git", "repositories", project=project))
        return data.get("value") or []

    # ---- pull requests ------------------------------------------------------------------------
    def _pr_base(self, organization: str, project: str, repository: str) -> str:
        return build_url(organization, "git", "repositories", repository, "pullrequests", project=project)

    async def list_pull_requests(
        self, organization: str, project: str, repository: str, status: str = "active", max_items: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._get_paged(
            self._pr_base(organization, project, repository), {"searchCriteria.status": status}, max_items=max_items
        )

    async def get_pull_request(self, organization: str, project: str, repository: str, pr_id: int) -> dict[str, Any]:
        return await self._get_json(f"{self._pr_base(organization, project, repository)}/{int(pr_id)}")

    async def list_pr_attachments(self, organization: str, project: str, repository: str, pr_id: int) -> list[dict[str, Any]]:
        data = await self._get_json(f"{self._pr_base(organization, project, repository)}/{int(pr_id)}/attachments")
        return data.get("value") or []

    async def get_pr_attachment(
        self, organization: str, project: str, repository: str, pr_id: int, file_name: str
    ) -> bytes:
        url = f"{self._pr_base(organization, project, repository)}/{int(pr_id)}/attachments/{quote(file_name, safe='')}"
        resp = await self._request(url, {}, accept="application/octet-stream")
        return resp.content

    async def list_pr_work_items(self, organization: str, project: str, repository: str, pr_id: int) -> list[dict[str, Any]]:
        data = await self._get_json(f"{self._pr_base(organization, project, repository)}/{int(pr_id)}/workitems")
        return data.get("value") or []

    async def list_iterations(self, organization: str, project: str, repository: str, pr_id: int) -> list[dict[str, Any]]:
        data = await self._get_json(f"{self._pr_base(organization, project, repository)}/{int(pr_id)}/iterations")
        return data.get("value") or []

    async def get_iteration_changes(
        self, organization: str, project: str, repository: str, pr_id: int, iteration_id: int
    ) -> list[dict[str, Any]]:
        url = f"{self._pr_base(organization, project, repository)}/{int(pr_id)}/iterations/{int(iteration_id)}/changes"
        entries: list[dict[str, Any]] = []
        skip = 0
        for _ in range(MAX_PAGES):
            data = await self._get_json(url, {"$top": PAGE_SIZE, "$skip": skip})
            entries.extend(data.get("changeEntries") or [])
            next_skip = data.get("nextSkip")
            if not next_skip:
                break
            skip = next_skip
        return entries

    async def list_commits(self, organization: str, project: str, repository: str, pr_id: int) -> list[dict[str, Any]]:
        url = f"{self._pr_base(organization, project, repository)}/{int(pr_id)}/commits"
        commits: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"$top": PAGE_SIZE}
            if token:
                params["continuationToken"] = token
            resp = await self._request(url, params)
            commits.extend(resp.json().get("value") or [])
            token = resp.headers.get("x-ms-continuationtoken")
            if not token:
                break
        return commits

    async def list_threads(self, organization: str, project: str, repository: str, pr_id: int) -> list[dict[str, Any]]:
        data = await self._get_json(f"{self._pr_base(organization, project, repository)}/{int(pr_id)}/threads")
        return data.get("value") or []

    async def get_file_text(
        self, organization: str, project: str, repository: str, path: str, commit_id: str
    ) -> FileContent:
        """Returns file text at a commit. text=None means missing (skipped=False) or binary/too large (skipped=True)."""
        url = build_url(organization, "git", "repositories", repository, "items", project=project)
        params = {
            "path": path,
            "versionDescriptor.version": commit_id,
            "versionDescriptor.versionType": "commit",
            "includeContent": "true",
            "$format": "text",
        }
        try:
            resp = await self._request(url, params, accept="text/plain")
        except AzureDevOpsError as exc:
            if exc.status_code == 404:
                return FileContent(None, skipped=False)
            raise
        raw = resp.content
        if len(raw) > MAX_FILE_BYTES or NUL in raw[:8000]:
            return FileContent(None, skipped=True)
        return FileContent(raw.decode("utf-8", errors="replace"), skipped=False)

    # ---- repositories: branches, files (any branch / commit) ----------------------------------
    async def get_repository(self, organization: str, project: str, repository: str) -> dict[str, Any]:
        return await self._get_json(build_url(organization, "git", "repositories", repository, project=project))

    async def list_branches(self, organization: str, project: str, repository: str) -> list[dict[str, Any]]:
        url = build_url(organization, "git", "repositories", repository, "refs", project=project)
        data = await self._get_json(url, {"filter": "heads/", "$top": 200})
        return data.get("value") or []

    async def list_items(
        self, organization: str, project: str, repository: str, path: str, branch: str | None, recursive: bool
    ) -> list[dict[str, Any]]:
        url = build_url(organization, "git", "repositories", repository, "items", project=project)
        params: dict[str, Any] = {"scopePath": path or "/", "recursionLevel": "Full" if recursive else "OneLevel"}
        if branch:
            params.update({"versionDescriptor.version": branch, "versionDescriptor.versionType": "branch"})
        data = await self._get_json(url, params)
        return data.get("value") or []

    async def get_file_bytes(
        self, organization: str, project: str, repository: str, path: str, branch: str | None
    ) -> bytes:
        """Raw file content at the head of a branch (default branch when branch is None)."""
        url = build_url(organization, "git", "repositories", repository, "items", project=project)
        params: dict[str, Any] = {"path": path, "download": "true", "$format": "octetStream"}
        if branch:
            params.update({"versionDescriptor.version": branch, "versionDescriptor.versionType": "branch"})
        resp = await self._request(url, params, accept="application/octet-stream")
        return resp.content

    # ---- work items (tasks, bugs, ...) --------------------------------------------------------
    async def query_work_item_ids(self, organization: str, project: str, wiql: str, top: int = 50) -> list[int]:
        url = build_url(organization, "wit", "wiql", project=project)
        resp = await self._request(url, {"$top": top}, json_body={"query": wiql})
        return [int(w["id"]) for w in resp.json().get("workItems") or []][:top]

    async def get_work_items(self, organization: str, ids: list[int], fields: list[str] | None = None) -> list[dict[str, Any]]:
        if not ids:
            return []
        url = build_url(organization, "wit", "workitems")
        params: dict[str, Any] = {"ids": ",".join(str(i) for i in ids[:200])}
        if fields:
            params["fields"] = ",".join(fields)
        data = await self._get_json(url, params)
        return data.get("value") or []

    async def get_work_item(self, organization: str, work_item_id: int) -> dict[str, Any]:
        url = build_url(organization, "wit", "workitems", str(int(work_item_id)))
        return await self._get_json(url, {"$expand": "relations"})

    async def list_work_item_comments(self, organization: str, project: str, work_item_id: int) -> list[dict[str, Any]]:
        url = build_url(organization, "wit", "workItems", str(int(work_item_id)), "comments", project=project)
        data = await self._get_json(url, {"api-version": "7.1-preview.4"})  # comments API is preview-only
        return data.get("comments") or []

    async def get_attachment(self, organization: str, attachment_id: str) -> bytes:
        url = build_url(organization, "wit", "attachments", attachment_id)
        resp = await self._request(url, {"download": "true"}, accept="application/octet-stream")
        return resp.content
