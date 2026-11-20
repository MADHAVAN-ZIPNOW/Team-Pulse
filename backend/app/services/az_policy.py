"""Which `az` commands Claude may run on a person's behalf.

Claude gets the Azure CLI signed in as the *person asking*. This policy is the guard-rail between "Claude can read" and
"Claude can change things". It is enforced in a PreToolUse hook, so a command that fails the policy never runs.

Levels (AZ_POLICY):
  read   list / show / query only
  write  read + create / update / add / set-vote / run   (no deletes)
  full   write + delete / remove / destroy
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

POLICIES = ("read", "write", "full")

# Only the Azure DevOps command groups. Everything else (login, account, ad, role, rest, extension, ...) is denied.
ALLOWED_GROUPS = {"boards", "repos", "pipelines", "devops"}
# Inside `az devops`, only read-only project listing is allowed (no invoke/login/configure/user/security/...).
DEVOPS_ALLOWED = {("devops", "project", "list"), ("devops", "project", "show")}

READ_VERBS = {"list", "show", "query", "get", "download", "list-definitions", "show-definition"}
WRITE_VERBS = {"create", "update", "add", "set-vote", "run", "queue"}
DELETE_VERBS = {"delete", "remove", "destroy"}

SHELL_METACHARS = re.compile(r"[;&|`$<>\n\r(){}]")
FORBIDDEN_FLAGS = {"--debug", "--verbose", "--help-all"}  # --debug prints request headers, including access tokens


@dataclass(frozen=True)
class AzContext:
    config_dir: str  # this person's private AZURE_CONFIG_DIR (their own az sign-in)
    org: str  # the selected Azure DevOps organization
    policy: str  # "read" | "write" | "full"


def describe(policy: str) -> str:
    return {
        "read": "read only (list, show, query)",
        "write": "read and change (create, update, add) but never delete",
        "full": "read, change and delete",
    }.get(policy, "read only")


def check_az_command(command: str, org: str, policy: str) -> tuple[bool, str]:
    """Returns (allowed, reason-if-denied). The reason is shown to Claude so it can adapt or tell the user."""
    if SHELL_METACHARS.search(command):
        return False, "Shell operators (; & | > < $ ` ( ) { } and newlines) are not allowed. Run one plain az command."
    try:
        argv = shlex.split(command)
    except ValueError:
        return False, "Could not parse the command."
    if not argv or argv[0] != "az":
        return False, "Only `az` commands are allowed."
    args = argv[1:]
    path: list[str] = []
    for token in args:  # the command path is the leading words before the first option
        if token.startswith("-"):
            break
        path.append(token)
    if not path:
        return False, "Give a full command such as `az boards work-item show --id 1 --org ...`."
    if path[0] not in ALLOWED_GROUPS:
        return False, f"`az {path[0]}` is not allowed. Only az boards, az repos, az pipelines and az devops project are available."
    if path[0] == "devops" and tuple(path[:3]) not in DEVOPS_ALLOWED:
        return False, "Only `az devops project list|show` is allowed in the devops group."

    flags = {a.split("=", 1)[0] for a in args if a.startswith("-")}
    if flags & FORBIDDEN_FLAGS:
        return False, "--debug and --verbose are not allowed because they can print credentials."

    verb = path[-1]
    destructive = verb in DELETE_VERBS or "--destroy" in flags
    if destructive:
        if policy != "full":
            return False, f"Deleting is disabled (policy: {describe(policy)}). Tell the user it is not allowed."
    elif verb in WRITE_VERBS:
        if policy == "read":
            return False, f"Changing things is disabled (policy: {describe(policy)}). Tell the user it is not allowed."
    elif verb not in READ_VERBS:
        return False, f"`{' '.join(path)}` is not a recognised read or change command, so it is not allowed."

    # Pin the organization so Claude cannot wander into other organizations.
    wanted = f"https://dev.azure.com/{org}".lower()
    given = None
    for i, a in enumerate(args):
        if a in ("--org", "--organization") and i + 1 < len(args):
            given = args[i + 1]
        elif a.startswith(("--org=", "--organization=")):
            given = a.split("=", 1)[1]
    if given is None:
        return False, f"Add `--org {wanted}` to the command."
    if given.rstrip("/").lower() != wanted:
        return False, f"Only the selected organization is allowed: --org {wanted}"
    return True, ""
