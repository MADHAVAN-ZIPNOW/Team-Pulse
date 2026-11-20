from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from ..auth.dependencies import get_ado_client
from ..azure_devops import models as m
from ..azure_devops.client import AzureDevOpsClient
from ..schemas.common import OrganizationQuery, ProjectQuery, RepositoryQuery
from ..services.pull_requests import get_pr_changes, latest_iteration

router = APIRouter(prefix="/api/azure", tags=["azure-devops"])

Client = Annotated[AzureDevOpsClient, Depends(get_ado_client)]
PrId = Annotated[int, Path(ge=1, le=2_147_483_647, alias="pull_request_id")]


@router.get("/organizations", response_model=list[m.Organization], response_model_by_alias=True)
async def organizations(client: Client) -> list[m.Organization]:
    raw = await client.list_organizations()
    return [m.Organization(name=o["accountName"], url=o.get("accountUri")) for o in raw if o.get("accountName")]


@router.get("/projects", response_model=list[m.Project], response_model_by_alias=True)
async def projects(client: Client, organization: OrganizationQuery) -> list[m.Project]:
    raw = await client.list_projects(organization)
    return [m.Project(id=p["id"], name=p["name"], description=p.get("description")) for p in raw]


@router.get("/repositories", response_model=list[m.Repository], response_model_by_alias=True)
async def repositories(client: Client, organization: OrganizationQuery, project: ProjectQuery) -> list[m.Repository]:
    raw = await client.list_repositories(organization, project)
    return [
        m.Repository(id=r["id"], name=r["name"], default_branch=r.get("defaultBranch"), web_url=r.get("webUrl"))
        for r in raw
        if not r.get("isDisabled")
    ]


@router.get("/pull-requests", response_model=list[m.PullRequestSummary], response_model_by_alias=True)
async def pull_requests(
    client: Client, organization: OrganizationQuery, project: ProjectQuery, repository: RepositoryQuery
) -> list[m.PullRequestSummary]:
    raw = await client.list_pull_requests(organization, project, repository)
    return [m.parse_pr_summary(pr) for pr in raw]


@router.get("/pull-requests/{pull_request_id}", response_model=m.PullRequestDetail, response_model_by_alias=True)
async def pull_request(
    client: Client,
    pull_request_id: PrId,
    organization: OrganizationQuery,
    project: ProjectQuery,
    repository: RepositoryQuery,
) -> m.PullRequestDetail:
    raw = await client.get_pull_request(organization, project, repository, pull_request_id)
    iterations = await client.list_iterations(organization, project, repository, pull_request_id)
    latest = latest_iteration(iterations)
    return m.parse_pr_detail(raw, latest["id"] if latest else None)


@router.get("/pull-requests/{pull_request_id}/changes", response_model=m.ChangesResponse, response_model_by_alias=True)
async def pull_request_changes(
    client: Client,
    pull_request_id: PrId,
    organization: OrganizationQuery,
    project: ProjectQuery,
    repository: RepositoryQuery,
) -> m.ChangesResponse:
    return await get_pr_changes(client, organization, project, repository, pull_request_id)


@router.get("/pull-requests/{pull_request_id}/commits", response_model=list[m.Commit], response_model_by_alias=True)
async def pull_request_commits(
    client: Client,
    pull_request_id: PrId,
    organization: OrganizationQuery,
    project: ProjectQuery,
    repository: RepositoryQuery,
) -> list[m.Commit]:
    raw = await client.list_commits(organization, project, repository, pull_request_id)
    return [m.parse_commit(c) for c in raw]


@router.get(
    "/pull-requests/{pull_request_id}/comments", response_model=list[m.CommentThread], response_model_by_alias=True
)
async def pull_request_comments(
    client: Client,
    pull_request_id: PrId,
    organization: OrganizationQuery,
    project: ProjectQuery,
    repository: RepositoryQuery,
) -> list[m.CommentThread]:
    raw = await client.list_threads(organization, project, repository, pull_request_id)
    return m.parse_threads(raw)
