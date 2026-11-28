"""Which Claude account the whole app uses, and what each shared account (person) has used through this app.

Memory only: counters restart with the server. They measure Team Pulse's own questions, not Anthropic's billing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque


log = logging.getLogger("pr_assistant.claude.usage")

FEATURES = ("PR Assistant", "Problem Radar", "Google Trends", "Meetings")


class ClaudeUsage:
    def __init__(self, labels: list[str], default_active: str = "", tokens: dict[str, str] | None = None, running=lambda: 0, state_file: str = "") -> None:
        self.labels = labels
        self.events: deque = deque(maxlen=50_000)
        self.started = time.time()
        self.auto_switch = True  # move to the next account automatically when the active one is nearly full
        self.switch_log: list[dict] = []  # newest last: {ts, from, to, reason}
        self.models: dict[str, str] = {}  # feature -> model id chosen by an admin; missing = the feature's own default
        self.running = running
        self._tokens = tokens or {}
        self._by_token = {t: l for l, t in self._tokens.items()}
        self._env_before = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")  # what the server was started with
        self.active: str | None = None
        self._state_file = state_file
        saved = self._load()
        self.auto_switch = bool(saved.get("autoSwitch", self.auto_switch))
        self.models = {k: v for k, v in (saved.get("models") or {}).items() if isinstance(k, str) and isinstance(v, str)}
        # Always have an account in use, so nobody is ever asked to paste a token: the admin's saved choice, else the default
        # account, else the first shared account (a better one is picked as soon as the limits are known).
        start = saved.get("active") if saved.get("active") in self._tokens else ""
        start = start or (default_active if default_active in self._tokens else "")
        self.admin_chose = bool(start)  # a saved or configured account counts as chosen; the app then keeps it
        if not start and self._tokens:
            start = next(iter(self._tokens))
        self._apply(start or None)

    # -- remembered choices (names and model ids only, never tokens) -------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self._state_file, encoding="utf8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def save(self) -> None:
        if not self._state_file:
            return
        try:
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf8") as fh:
                json.dump({"active": self.active, "autoSwitch": self.auto_switch, "models": self.models}, fh)
            os.replace(tmp, self._state_file)
        except OSError:
            log.warning("Could not remember the Claude settings on disk (this server's disk may be read-only).")

    def set_auto(self, enabled: bool) -> None:
        self.auto_switch = enabled
        self.save()

    def set_model(self, feature: str, model: str | None) -> None:
        if model:
            self.models[feature] = model
        else:
            self.models.pop(feature, None)
        self.save()

    def set_active(self, label: str | None, chosen: bool = True) -> None:
        """Make one shared account the app-wide one (an admin's choice or an automatic switch). It stays until an admin or the automatic
        switch changes it. With shared accounts available there is ALWAYS one in use, so label=None changes nothing: nobody is ever left
        without an account or asked to paste a token."""
        if label is None and self._tokens:
            return
        self._apply(label)
        if chosen:
            self.admin_chose = True
        self.save()

    def _apply(self, label: str | None) -> None:
        """Make one account the app-wide one. Every Claude process the app starts (chat, Radar, Trends, Meetings, and any
        feature added later) inherits CLAUDE_CODE_OAUTH_TOKEN from the server's environment, so setting it here covers them all."""
        self.active = label if label in self._tokens else None
        if self.active:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = self._tokens[self.active]
        elif self._env_before is None:
            os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        else:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = self._env_before

    def record_event(self, ev: dict) -> None:
        """One finished Claude call from any feature. Memory only; restarts clear it."""
        token = ev.get("token")
        label = self._by_token.get(token) if token else None
        err = ev.get("error") or ""
        low = err.lower()
        self.events.append({
            "ts": time.time(), "label": label or ("Own token" if token else "Server login"), "feature": ev.get("feature", "App"), "model": ev.get("model") or "",
            "ok": bool(ev.get("ok")), "input": int(ev.get("input", 0)), "output": int(ev.get("output", 0)),
            "cost": float(ev.get("cost", 0.0)), "latency": float(ev.get("latency", 0.0)),
            "r429": (not ev.get("ok")) and ("429" in err or "rate limit" in low or "usage limit" in low or "limit reached" in low),
        })

    def summary(self, range_id: str, account: str | None) -> dict:
        """Everything the dashboard shows for one time range, from the recorded calls."""
        span, n = {"1h": (3600, 12), "24h": (86400, 24), "7d": (7 * 86400, 7), "30d": (30 * 86400, 15)}.get(range_id, (86400, 24))
        now = time.time()
        start, prev_start = now - span, now - 2 * span
        pick = lambda e: account is None or e["label"] == account
        cur = [e for e in self.events if e["ts"] >= start and pick(e)]
        prev = [e for e in self.events if prev_start <= e["ts"] < start and pick(e)]

        def totals(evs: list[dict]) -> dict:
            req = len(evs)
            failed = sum(1 for e in evs if not e["ok"])
            return {"requests": req, "tokens": sum(e["input"] + e["output"] for e in evs), "cost": round(sum(e["cost"] for e in evs), 4),
                    "rateLimited": sum(1 for e in evs if e["r429"]), "errorRate": round(100 * failed / req, 2) if req else 0.0}

        size = span / n
        series = [{"ts": start + i * size, "requests": 0, "cost": 0.0, "input": 0, "output": 0} for i in range(n)]
        for e in cur:
            b = series[min(n - 1, int((e["ts"] - start) / size))]
            b["requests"] += 1
            b["cost"] += e["cost"]
            b["input"] += e["input"]
            b["output"] += e["output"]
        for b in series:
            b["cost"] = round(b["cost"], 4)

        feats: dict[str, list[dict]] = {name: [] for name in FEATURES}
        for e in cur:
            feats.setdefault(e["feature"], []).append(e)
        total_tokens = sum(e["input"] + e["output"] for e in cur) or 1
        features = []
        for k, v in feats.items():
            calls = len(v)
            tok = sum(x["input"] + x["output"] for x in v)
            cost = sum(x["cost"] for x in v)
            features.append({
                "name": k, "calls": calls, "tokens": tok, "inputTokens": sum(x["input"] for x in v), "outputTokens": sum(x["output"] for x in v),
                "cost": round(cost, 4), "share": round(100 * tok / total_tokens, 1),
                "tokensPerCall": round(tok / calls) if calls else 0, "costPerCall": round(cost / calls, 4) if calls else 0.0,
                "failRate": round(100 * sum(1 for x in v if not x["ok"]) / calls, 1) if calls else 0.0,
                "avgLatency": round(sum(x["latency"] for x in v) / calls, 1) if calls else 0.0,
                "model": next((x["model"] for x in reversed(v) if x["model"]), ""),
            })
        features.sort(key=lambda f: -f["tokens"])

        quarter = now - 900
        accounts = []
        for label in [*self.labels, *sorted({e["label"] for e in self.events} - set(self.labels))]:
            mine = [e for e in self.events if e["label"] == label and e["ts"] >= start]
            accounts.append({
                "name": label, "pool": label in self.labels, "active": label == self.active, **totals(mine),
                "inputTokens": sum(e["input"] for e in mine), "outputTokens": sum(e["output"] for e in mine),
                "rateLimited15m": sum(1 for e in mine if e["r429"] and e["ts"] >= quarter),
                "lastUsed": max((e["ts"] for e in self.events if e["label"] == label), default=None),
            })
        c = totals(cur)
        failed = sum(1 for e in cur if not e["ok"])
        return {
            "range": range_id, "totals": c, "previous": totals(prev), "series": series, "features": features, "accounts": accounts,
            "jobs": {"total": c["requests"], "ok": c["requests"] - failed, "failed": failed, "running": self.running()},
            "since": self.started,
        }


_LIMITS_TTL = 60.0  # seconds; each probe is a real (1-token) request, so don't repeat it more often than this


async def probe_limits(http, token: str) -> dict | None:
    """Read the plan limits for one token: a 1-token request whose response headers say how full the 5-hour and weekly windows are."""
    try:
        res = await http.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-haiku-4-5", "max_tokens": 1,
                "system": "You are Claude Code, Anthropic's official CLI for Claude.",
                "messages": [{"role": "user", "content": "hi"}],
            },
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - network trouble just means "no limits shown"
        return None
    h = res.headers

    def window(prefix: str) -> dict | None:
        util, reset = h.get(f"anthropic-ratelimit-unified-{prefix}-utilization"), h.get(f"anthropic-ratelimit-unified-{prefix}-reset")
        if util is None:
            return None
        return {"usedPercent": round(float(util) * 100, 1), "resetsAt": int(reset) if reset else None, "status": h.get(f"anthropic-ratelimit-unified-{prefix}-status", "")}

    if res.status_code >= 400 and "anthropic-ratelimit-unified-5h-utilization" not in h:
        return None
    return {"fiveHour": window("5h"), "sevenDay": window("7d"), "checkedAt": time.time()}


class LimitsCache:
    def __init__(self) -> None:
        self._data: dict[str, dict | None] = {}
        self._at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def get(self, http, label: str, token: str, force: bool = False) -> dict | None:
        async with self._lock:
            if force or time.time() - self._at.get(label, 0) > _LIMITS_TTL:
                self._data[label] = await probe_limits(http, token)
                self._at[label] = time.time()
            return self._data.get(label)
