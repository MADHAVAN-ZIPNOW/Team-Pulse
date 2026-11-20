"""Azure CLI sign-in mode: reuse `az login` (device code) as the source of Azure DevOps tokens."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from ..config import AZURE_DEVOPS_RESOURCE_ID

logger = logging.getLogger("pr_assistant.azure_cli")
CODE_RE = re.compile(r"enter the code\s+([A-Z0-9]+)", re.IGNORECASE)
URL_RE = re.compile(r"https://[^\s]*devicelogin[^\s]*", re.IGNORECASE)


class AzureCliError(Exception):
    """Message is safe to show to the user."""


def az_path() -> str:
    found = os.getenv("AZ_CLI_PATH", "").strip() or shutil.which("az")
    if not found and sys.platform == "win32":
        found = shutil.which("az.cmd") or shutil.which("az.bat")
        local = Path.home() / ".azcli" / "Scripts" / "az.bat"  # uv/pip install location (no admin needed)
        if not found and local.exists():
            found = str(local)
    if not found:
        raise AzureCliError("Azure CLI not found. Install it (winget install Microsoft.AzureCLI) and restart the backend.")
    return found


async def _run(*args: str, timeout: float = 60) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        az_path(), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise AzureCliError("Azure CLI timed out.")
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


_token_cache: dict[str, float | str] = {}
_token_lock = asyncio.Lock()


async def get_access_token() -> str:
    """Token from `az`, cached in memory until ~5 min before expiry (spawning `az` takes seconds)."""
    now = time.time()
    if _token_cache and float(_token_cache["expires"]) - 300 > now:
        return str(_token_cache["token"])
    async with _token_lock:
        if _token_cache and float(_token_cache["expires"]) - 300 > time.time():
            return str(_token_cache["token"])
        code, out, _ = await _run(
            "account", "get-access-token", "--resource", AZURE_DEVOPS_RESOURCE_ID, "--output", "json"
        )
        if code != 0:
            _token_cache.clear()
            raise AzureCliError("Not signed in to Azure CLI. Sign in again.")
        data = json.loads(out)
        expires = float(data.get("expires_on") or (time.time() + 1800))
        _token_cache.update(token=data["accessToken"], expires=expires)
        return str(data["accessToken"])


def clear_token_cache() -> None:
    _token_cache.clear()


async def current_user() -> dict[str, str | None] | None:
    """Returns {name, email, tenantId} for the signed-in az account, or None."""
    code, out, _ = await _run("account", "show", "--output", "json")
    if code != 0:
        return None
    data = json.loads(out)
    user = (data.get("user") or {}).get("name") or "Azure CLI user"
    return {"name": user, "email": user, "tenantId": data.get("tenantId")}


async def start_device_login() -> dict[str, str]:
    """Spawns `az login --use-device-code` and returns the code + URL the user must open."""
    proc = await asyncio.create_subprocess_exec(
        az_path(), "login", "--use-device-code", "--allow-no-subscriptions", "--output", "none",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stderr is not None
    stderr = proc.stderr
    buffer = ""

    async def read() -> dict[str, str]:
        nonlocal buffer
        while True:
            chunk = await stderr.read(512)
            if not chunk:
                raise AzureCliError("Azure CLI did not provide a sign-in code.")
            buffer += chunk.decode(errors="replace")
            m = CODE_RE.search(buffer)
            if m:
                url = URL_RE.search(buffer)
                return {"code": m.group(1), "url": url.group(0).rstrip(".") if url else "https://microsoft.com/devicelogin"}

    try:
        result = await asyncio.wait_for(read(), 30)
    except Exception as exc:
        proc.kill()
        if isinstance(exc, AzureCliError):
            raise
        raise AzureCliError("Timed out waiting for Azure CLI to provide a sign-in code.") from exc
    # Let the CLI finish in the background once the user completes sign-in (or give up after 15 min).
    asyncio.create_task(_wait_and_reap(proc))
    return result


async def _wait_and_reap(proc: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(proc.wait(), 900)
    except asyncio.TimeoutError:
        proc.kill()
