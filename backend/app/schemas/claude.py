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


class ClaudeStatus(BaseModel):
    required: bool  # True when each person must connect their own Claude account (hosted mode)
    hasToken: bool
