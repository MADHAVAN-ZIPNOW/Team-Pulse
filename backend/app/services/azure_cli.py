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


def cli_env(config_dir: str | None) -> dict[str, str]:
    """Environment for an az process. A per-person config dir keeps each person's sign-in separate."""
    env = dict(os.environ)
    if config_dir:
        env["AZURE_CONFIG_DIR"] = config_dir
    env["AZURE_CORE_COLLECT_TELEMETRY"] = "false"
    env["AZURE_CORE_NO_COLOR"] = "true"
    return env


async def _run(*args: str, timeout: float = 60, config_dir: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        az_path(), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=cli_env(config_dir)
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise AzureCliError("Azure CLI timed out.")
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


_token_cache: dict[str, dict[str, float | str]] = {}  # keyed by az config dir ("" = the machine default)
_token_lock = asyncio.Lock()


async def get_access_token(config_dir: str | None = None) -> str:
    """Azure DevOps token from `az`, cached in memory until ~5 min before expiry (spawning `az` takes seconds).

    config_dir selects whose az sign-in to use: each person has their own folder when AUTH_MODE=az_login."""
    key = config_dir or ""
    cached = _token_cache.get(key)
    if cached and float(cached["expires"]) - 300 > time.time():
        return str(cached["token"])
    async with _token_lock:
        cached = _token_cache.get(key)
        if cached and float(cached["expires"]) - 300 > time.time():
            return str(cached["token"])
        code, out, _ = await _run(
            "account", "get-access-token", "--resource", AZURE_DEVOPS_RESOURCE_ID, "--output", "json",
            config_dir=config_dir,
        )
        if code != 0:
            _token_cache.pop(key, None)
            raise AzureCliError("Not signed in to Azure CLI. Sign in again.")
        data = json.loads(out)
        expires = float(data.get("expires_on") or (time.time() + 1800))
        _token_cache[key] = {"token": data["accessToken"], "expires": expires}
        return str(data["accessToken"])


def clear_token_cache(config_dir: str | None = None) -> None:
    _token_cache.pop(config_dir or "", None)


async def current_user(config_dir: str | None = None) -> dict[str, str | None] | None:
    """Returns {name, email, tenantId} for the signed-in az account, or None."""
    code, out, _ = await _run("account", "show", "--output", "json", config_dir=config_dir)
    if code != 0:
        return None
    data = json.loads(out)
    user = (data.get("user") or {}).get("name") or "Azure CLI user"
    return {"name": user, "email": user, "tenantId": data.get("tenantId")}


_login_procs: dict[str, asyncio.subprocess.Process] = {}  # running `az login`, keyed by config dir


def login_state(config_dir: str | None) -> str:
    """"running" while the person has not finished the code sign-in, "done" / "failed" once `az login` exited,
    "unknown" if we are not tracking one (then the caller falls back to asking `az account show`)."""
    proc = _login_procs.get(config_dir or "")
    if proc is None:
        return "unknown"
    if proc.returncode is None:
        return "running"
    _login_procs.pop(config_dir or "", None)
    return "done" if proc.returncode == 0 else "failed"


async def start_device_login(config_dir: str | None = None, tenant: str | None = None) -> dict[str, str]:
    """Spawns `az login --use-device-code` and returns the code + URL the user must open."""
    args = ["login", "--use-device-code", "--allow-no-subscriptions", "--output", "none"]
    if tenant:
        args += ["--tenant", tenant]  # only accounts from our company directory can finish this sign-in
    proc = await asyncio.create_subprocess_exec(
        az_path(), *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, env=cli_env(config_dir),
    )
    assert proc.stderr is not None
    stderr = proc.stderr
    _login_procs[config_dir or ""] = proc
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
