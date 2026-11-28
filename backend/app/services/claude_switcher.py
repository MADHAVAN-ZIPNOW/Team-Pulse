"""Keeps the whole app on a Claude account that still has room.

When the account everyone uses reaches CLAUDE_SWITCH_PERCENT (90%) of its 5-hour or weekly limit, the app moves to the shared
account with the most room left, and keeps doing that as accounts fill up, so nobody waits or is asked to paste a token.
Each change is emailed (and so is an admin's manual choice). Emails never contain tokens, only names and percentages.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time

import httpx

from .claude_usage import ClaudeUsage, LimitsCache

logger = logging.getLogger("pr_assistant.claude.switch")
CHECK_EVERY = 30  # seconds between looks at the active account (each look reads the cached limits, at most one request a minute per account)


def _used(limits: dict | None) -> tuple[float | None, str]:
    """Highest of the 5-hour and weekly usage, and which one it is."""
    best, which = None, ""
    for key, name in (("fiveHour", "5-hour"), ("sevenDay", "weekly")):
        w = (limits or {}).get(key)
        if w and (best is None or w["usedPercent"] > best):
            best, which = w["usedPercent"], name
    return best, which


class AccountSwitcher:
    def __init__(self, usage: ClaudeUsage, limits: LimitsCache, http: httpx.AsyncClient, tokens: dict[str, str], mailer, notify_to: list[str], percent: int = 90) -> None:
        self.usage, self.limits, self.http, self.tokens, self.mailer, self.notify_to, self.percent = usage, limits, http, tokens, mailer, notify_to, percent
        self._lock = asyncio.Lock()
        self._last_full_mail = 0.0
        self._last_off_mail = 0.0

    async def snapshot(self, force: bool = False) -> dict[str, tuple[float | None, str]]:
        labels = list(self.tokens)
        results = await asyncio.gather(*(self.limits.get(self.http, l, self.tokens[l], force) for l in labels))
        return {l: _used(r) for l, r in zip(labels, results)}

    async def check(self, force: bool = False) -> str | None:
        """If the active account is at the limit, move to the best other account. Returns the new account's name, or None."""
        async with self._lock:
            u = self.usage
            if not u.active:
                return None
            snap = await self.snapshot(force)
            cur, which = snap.get(u.active, (None, ""))
            if cur is None or cur < self.percent:
                return None
            if not u.auto_switch:  # switching is off: don't move anyone, but do tell people (at most once an hour)
                if time.time() - self._last_off_mail > 3600:
                    self._last_off_mail = time.time()
                    await self._mail(
                        f"Claude account nearly full: {u.active} at {cur:.0f}% of its {which} limit",
                        "<p>Hello,</p>"
                        f"<p><b>{html.escape(u.active)}</b>&#8217;s Claude account, which everyone is using, has reached <b>{cur:.0f}%</b> of its {which} limit. "
                        "Automatic switching is turned off, so nobody has been moved. Please choose another account on the Claude Monitor page, "
                        "or turn auto-switch back on.</p>",
                        snap,
                    )
                return None
            old = u.active
            roomy = [(v, l) for l, (v, _) in snap.items() if l != old and v is not None and v < self.percent]
            if roomy:
                new = min(roomy)[1]  # the account with the most room left
            else:
                best = min(((v, l) for l, (v, _) in snap.items() if l != old and v is not None), default=None)
                if best is None or best[0] >= cur:
                    await self._warn_all_full(snap)
                    return None
                new = best[1]
            await self._apply(old, new, f"{old} reached {cur:.0f}% of its {which} limit", snap, old_used=(cur, which))
            return new

    async def pick_best(self, force: bool = False) -> str | None:
        """Use the shared account with the most room left. Without force (the background check after a restart) it is silent and does not
        shuffle people for a small difference; with force (an admin asked for "automatic") it takes the best account whatever the gap."""
        async with self._lock:
            u = self.usage
            if not self.tokens:
                return None
            if not force and u.admin_chose:
                return None  # an admin's pick (or a saved one) stays until an admin or the 90% switch changes it
            snap = await self.snapshot()
            known = [(v, l) for l, (v, _) in snap.items() if v is not None]
            if not known:
                return None
            best = min(known)[1]
            if u.active == best:
                return None
            if not force and u.active and snap.get(u.active, (None, ""))[0] is not None and snap[u.active][0] < self.percent and snap[u.active][0] <= min(known)[0] + 15:
                return None  # the account in use is fine, don't shuffle people around for a small difference
            before = u.active
            u.set_active(best, chosen=False)
            self._log(before, best, "Picked automatically (nobody had chosen an account)")
            logger.info("App-wide Claude account picked automatically: %s -> %s", before, best)
            return best

    async def chosen_by_admin(self, old: str | None, new: str | None, by: str) -> None:
        """An admin pressed the button: record it and tell people."""
        if new == old:
            return
        snap = await self.snapshot()
        if new:
            await self._apply(old, new, f"Chosen by {by}", snap, switch=False)

    # ------------------------------------------------------------------------------------------------------------
    async def _apply(self, old: str | None, new: str, reason: str, snap, switch: bool = True, old_used: tuple[float, str] | None = None) -> None:
        if switch:
            self.usage.set_active(new)
        self._log(old, new, reason)
        logger.info("App-wide Claude account: %s -> %s (%s)", old, new, reason)
        nv, nw = snap.get(new, (None, ""))
        new_part = f"{html.escape(new)}&#8217;s account is currently at <b>{nv:.0f}%</b> of its {nw} limit." if nv is not None else ""
        if switch and old and old_used:
            subject = f"Claude account switched: {old} → {new} ({old} at {old_used[0]:.0f}% of its {old_used[1]} limit)"
            lead = (
                "<p>Hello,</p>"
                f"<p>Because <b>{html.escape(old)}</b>&#8217;s Claude account reached <b>{old_used[0]:.0f}%</b> of its {old_used[1]} limit, "
                f"we have switched to <b>{html.escape(new)}</b>&#8217;s account so that nobody has to wait. {new_part}</p>"
                f"<p>Right now <b>everyone who signs in</b> uses <b>{html.escape(new)}</b>&#8217;s Claude account, for every feature. Thank you.</p>"
            )
        else:
            subject = f"Claude account in use: {new}"
            lead = (
                "<p>Hello,</p>"
                f"<p>{html.escape(reason)}. {new_part}</p>"
                f"<p>Right now <b>everyone who signs in</b> uses <b>{html.escape(new)}</b>&#8217;s Claude account, for every feature. Thank you.</p>"
            )
        await self._mail(subject, lead, snap)

    def _log(self, old, new, reason: str) -> None:
        self.usage.switch_log.append({"ts": time.time(), "from": old, "to": new, "reason": reason})
        del self.usage.switch_log[:-20]

    async def _warn_all_full(self, snap) -> None:
        if time.time() - self._last_full_mail < 3600:
            return
        self._last_full_mail = time.time()
        await self._mail(
            "Claude accounts: all are nearly full",
            f"<p><b>Every Claude account is at or above {self.percent}%.</b> The app stays on {html.escape(str(self.usage.active))}; "
            "requests may be refused until a limit resets.</p>",
            snap,
        )

    async def _mail(self, subject: str, lead_html: str, snap) -> None:
        if not (self.mailer and self.notify_to):
            return
        rows = "".join(
            f'<tr><td style="padding:4px 12px 4px 0"><b>{html.escape(l)}</b>{" (in use)" if l == self.usage.active else ""}</td>'
            f'<td style="padding:4px 0">{"unknown" if v is None else f"{v:.0f}% of its {w} limit"}</td></tr>'
            for l, (v, w) in snap.items()
        )
        body = (
            '<div style="font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;color:#201f1e">'
            f"{lead_html}"
            f'<table style="border-collapse:collapse;font-size:13px;margin-top:8px">{rows}</table>'
            '<p style="color:#605e5c;font-size:12px;margin-top:14px">Team Pulse &middot; Claude Monitor. Automatic switching happens at '
            f"{self.percent}%.</p></div>"
        )
        try:
            await self.mailer.send_message(subject, body, self.notify_to)
        except Exception as exc:  # noqa: BLE001 - a mail problem must never block switching
            logger.warning("Could not send the Claude account email: %s", exc)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.pick_best()
                await asyncio.sleep(CHECK_EVERY)
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Claude account check failed")
