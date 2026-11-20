from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from .common import CamelModel, NameStr, OrgStr

QuestionStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class AskRequest(CamelModel):
    """Ask about a pull request (repository + pullRequestId), a repository, or the whole project."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=to_camel)

    organization: OrgStr
    project: NameStr
    repository: NameStr | None = None
    pull_request_id: int | None = Field(default=None, ge=1, le=2_147_483_647)
    question: QuestionStr
    model: str | None = Field(default=None, max_length=64)  # one of the ids from GET /api/claude/models

    @model_validator(mode="after")
    def _pr_needs_repository(self) -> "AskRequest":
        if self.pull_request_id is not None and not self.repository:
            raise ValueError("repository is required when pullRequestId is given")
        return self


class AskResponse(CamelModel):
    answer: str
    pull_request_id: int | None = None
    files_analyzed: int = 0
    files_total: int = 0
    context_truncated: bool = False
