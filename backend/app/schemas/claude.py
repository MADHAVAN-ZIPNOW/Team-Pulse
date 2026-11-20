from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# Tokens from `claude setup-token` look like sk-ant-oat01-... (URL-safe characters only).
ClaudeTokenStr = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^sk-ant-[A-Za-z0-9_-]{20,400}$")
]


class ClaudeTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: ClaudeTokenStr


class ClaudeSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class ClaudeStatus(BaseModel):
    required: bool  # True when each person must connect a Claude account (hosted mode)
    hasToken: bool  # this person has saved their own token
    source: str  # "own" or the label of the shared account currently selected
    pool: list[str]  # labels of shared accounts the owner made available (never the tokens)
