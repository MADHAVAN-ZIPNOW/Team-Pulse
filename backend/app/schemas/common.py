"""Shared validated types and the API error envelope."""
from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, StringConstraints
from pydantic.alias_generators import to_camel

# Azure DevOps names: no control chars or path separators. Everything is URL-quoted later.
NAME_PATTERN = r"^[^\x00-\x1f/\?#%]+$"

OrganizationQuery = Annotated[str, Query(min_length=1, max_length=128, pattern=NAME_PATTERN)]
ProjectQuery = Annotated[str, Query(min_length=1, max_length=256, pattern=NAME_PATTERN)]
RepositoryQuery = Annotated[str, Query(min_length=1, max_length=256, pattern=NAME_PATTERN)]

NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256, pattern=NAME_PATTERN)]
OrgStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128, pattern=NAME_PATTERN)]


class CamelModel(BaseModel):
    """Base model that serializes to camelCase and accepts either casing on input."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ErrorResponse(BaseModel):
    detail: str
