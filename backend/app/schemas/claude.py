from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")
_LOGIN_CODE_RE = re.compile(r"[A-Za-z0-9_-]{20,}#[A-Za-z0-9_-]{20,}")  # the "code#state" shown after browser login
_OAT_LENGTH = 108  # `claude setup-token` tokens (sk-ant-oat01-...) are exactly 108 characters


def normalize_claude_token(raw: str) -> str | None:
    """Pull a Claude token out of whatever was pasted.

    Terminals often wrap a long token and the copy picks up spaces, line breaks or quotes; people also paste the whole
    `export CLAUDE_CODE_OAUTH_TOKEN=...` line. Remove all whitespace, find the sk-ant-... run, and drop trailing text
    that was glued on when the whitespace was removed.
    """
    compact = re.sub(r"\s+", "", raw or "")
    m = _TOKEN_RE.search(compact)
    if not m:
        return None
    token = m.group(0)
    if token.startswith("sk-ant-oat01-") and len(token) > _OAT_LENGTH:
        token = token[:_OAT_LENGTH]
    return token if len(token) <= 400 else None


class ClaudeTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, StringConstraints(max_length=4000)]

    @field_validator("token")
    @classmethod
    def _clean(cls, value: str) -> str:
        token = normalize_claude_token(value)
        if token is None and _LOGIN_CODE_RE.fullmatch(re.sub(r"\s+", "", value)):
            raise ValueError(
                "that is a one-time login code, not a token. Paste it into the terminal window where "
                "`claude setup-token` is waiting; the token that terminal prints afterwards starts with sk-ant-"
            )
        if token is None:
            raise ValueError(
                "that does not look like a Claude token. Paste the whole line printed by `claude setup-token` (it starts with sk-ant-)"
            )
        return token


class ClaudeSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class ClaudeStatus(BaseModel):
    required: bool  # True when each person must connect a Claude account (hosted mode)
    hasToken: bool  # this person has saved their own token
    source: str  # "own" or the label of the shared account currently selected
    activeAccount: str | None = None  # set by an admin: everyone's questions run on this shared account
    isAdmin: bool = False
    pool: list[str]  # labels of shared accounts the owner made available (never the tokens)


class ClaudeActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None  # a pool label, or null to let people choose


class ClaudeFeatureModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    model: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None  # a model id, or null for the feature's default
