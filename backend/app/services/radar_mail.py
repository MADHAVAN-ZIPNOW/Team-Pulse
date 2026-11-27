"""Emails a Problem Radar report. Sent through Microsoft Graph as the app itself (no one has to be signed in at 8am), from one
configured mailbox. This needs the application permission Mail.Send, approved by a Microsoft 365 admin.

The report text comes from web research, so everything is HTML-escaped and only http(s) links are used. The layout is table-based
with inline styles only (that is what Outlook, Gmail and phones all understand): no scripts, no web fonts, no remote images.
"""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

import httpx
import msal

logger = logging.getLogger("pr_assistant.radar.mail")

# severity -> (text colour, tint, label)
SEV = {
    "critical": ("#b42318", "#fee4e2", "CRITICAL"),
    "high": ("#c4320a", "#ffead5", "HIGH"),
    "medium": ("#b54708", "#fef0c7", "MEDIUM"),
    "low": ("#175cd3", "#d1e9ff", "LOW"),
}
# where a page came from -> (label, colour)
PLATFORM = {
    "reddit": ("Reddit", "#ff4500"), "x": ("X", "#101828"), "stack_overflow": ("Stack Overflow", "#c2410c"),
    "hacker_news": ("Hacker News", "#ea580c"), "medium": ("Medium", "#344054"), "devto": ("Dev.to", "#3b49df"),
    "github_issue": ("GitHub", "#24292f"), "official_docs": ("Official docs", "#175cd3"),
    "security_advisory": ("Security advisory", "#b42318"), "status_page": ("Status page", "#067647"), "blog": ("Blog", "#475467"),
    "forum": ("Forum", "#6941c6"),
}
FONT = "Segoe UI,Helvetica,Arial,sans-serif"
NO_PAGE = '<div style="color:#98a2b3;font-size:12px;font-style:italic">no page was recorded</div>'
OWN_FIX = '<div style="color:#98a2b3;font-size:12px;font-style:italic">Claude’s own suggestion: test it before production use.</div>'


def _e(v: Any) -> str:
    return html.escape(str(v or ""), quote=True)


def _sev(p: dict[str, Any]) -> tuple[str, str, str]:
    return SEV.get(str(p.get("severity")), ("#475467", "#f2f4f7", str(p.get("severity") or "UNRATED").upper()))


def _pill(text: str, fg: str, bg: str) -> str:
    return f'<span style="display:inline-block;background:{bg};color:{fg};font-size:11px;font-weight:700;letter-spacing:.4px;padding:2px 8px;border-radius:10px">{_e(text)}</span>'


def _chip(source_type: Any) -> str:
    label, color = PLATFORM.get(str(source_type or ""), (str(source_type or "source").replace("_", " ").title(), "#475467"))
    return f'<span style="display:inline-block;background:{color};color:#ffffff;font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px">{_e(label)}</span>'


def _links(items: list[dict[str, Any]] | None, empty: str = NO_PAGE) -> str:
    out = []
    for s in items or []:
        url = str(s.get("url") or "")
        if not url.startswith(("https://", "http://")):
            continue
        finding = ""
        if s.get("key_finding"):
            finding = f'<div style="color:#667085;font-size:12px;line-height:1.4;margin-top:1px">{_e(s.get("key_finding"))}</div>'
        out.append(
            f'<div style="margin:0 0 7px">{_chip(s.get("source_type"))} '
            f'<a href="{_e(url)}" style="color:#175cd3;font-size:13px;font-weight:600;text-decoration:none">{_e(s.get("title") or url)} ↗</a>{finding}</div>'
        )
    return "".join(out) or empty


def subject(report: dict[str, Any]) -> str:
    n = len(report.get("problems") or [])
    return f"Problem Radar · {report.get('report_date', '')} {str(report.get('slot', '')).title()} · {n} problem{'s' if n != 1 else ''}"


def _quote(label: str, text: Any, accent: str) -> str:
    if not text:
        return ""
    return (
        '<div style="margin:8px 0;padding:8px 11px;background:#ffffff;border:1px solid #eaecf0;border-radius:6px;font-size:13px;line-height:1.5;color:#344054">'
        f'<span style="color:{accent};font-weight:700">{_e(label)}</span> <i>{_e(text)}</i></div>'
    )


def _card(p: dict[str, Any]) -> str:
    fg, bg, name = _sev(p)
    a = p.get("recommended_action") or {}
    steps = "".join(f'<li style="margin:0 0 3px">{_e(s)}</li>' for s in (a.get("steps") or []))
    tags = [_pill(name, fg, bg)]
    for key in ("domain", "technology"):
        if p.get(key):
            tags.append(_pill(str(p[key]), "#344054", "#f2f4f7"))
    if p.get("confidence"):
        tags.append(f'<span style="color:#667085;font-size:12px">{_e(p.get("confidence"))} confidence</span>')
    dots = {"strong": "●●●", "moderate": "●●○", "weak": "●○○"}.get(str(p.get("evidence_strength")))
    if dots:
        tags.append(f'<span style="color:#667085;font-size:12px">evidence <span style="color:#3b5bdb;letter-spacing:1px">{dots}</span> {_e(p.get("evidence_strength"))}</span>')
    code = ""
    if a.get("code_or_config"):
        code = (
            '<pre style="margin:8px 0;padding:9px;background:#101828;color:#e4e7ec;border-radius:6px;font-size:12px;line-height:1.45;'
            f'white-space:pre-wrap;font-family:Consolas,Menlo,monospace">{_e(a.get("code_or_config"))}</pre>'
        )
    steps_html = f'<ol style="margin:8px 0 0 18px;padding:0;font-size:13px;line-height:1.5;color:#344054">{steps}</ol>' if steps else ""
    fix_text = _e(a.get("summary")) or "<i>No fix was found yet.</i>"
    seen = _links(p.get("problem_sources") or p.get("sources"))
    found = _links(p.get("solution_sources"), OWN_FIX)
    label_css = "margin-top:9px;font-size:11px;font-weight:700;letter-spacing:.5px;color:#667085"

    def facts(rows: list[tuple[str, Any]]) -> str:
        return "".join(
            f'<div style="margin-top:6px;font-size:13px;line-height:1.5;color:#344054"><b style="color:#101828">{_e(k)}:</b> {_e(v)}</div>' for k, v in rows if v
        )

    def lst(title: str, items: Any, mark: str, color: str) -> str:
        body = "".join(
            f'<div style="margin:2px 0;font-size:13px;line-height:1.5;color:#344054"><span style="color:{color};font-weight:700">{mark}</span> {_e(i)}</div>'
            for i in (items or [])
        )
        return f'<div style="{label_css}">{title}</div>{body}' if body else ""

    alt = "".join(
        f'<div style="margin:3px 0;font-size:13px;line-height:1.5;color:#344054">↳ {_e(w.get("summary"))}'
        + (f' <span style="color:#667085">(use when {_e(w.get("when_to_use"))})</span>' if w.get("when_to_use") else "")
        + "</div>"
        for w in (p.get("alternative_workarounds") or [])
        if isinstance(w, dict) and w.get("summary")
    )
    problem_extra = facts([("Who is affected", p.get("affected_versions_or_services")), ("Root cause", p.get("root_cause"))]) + lst("SYMPTOMS", p.get("symptoms"), "•", "#b42318")
    solution_extra = (
        lst("CHECK IT WORKED", a.get("verification_steps"), "✓", "#067647")
        + lst("WATCH OUT", a.get("risks"), "⚠", "#b54708")
        + (f'<div style="{label_css}">OTHER WORKAROUNDS</div>{alt}' if alt else "")
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;border:1px solid #d0d5dd;border-radius:10px;background:#ffffff"><tr><td style="padding:16px 18px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td width="38" valign="top"><div style="width:30px;height:30px;line-height:30px;text-align:center;background:#101828;color:#ffffff;border-radius:15px;font-weight:700;font-size:14px">{_e(p.get("rank"))}</div></td>'
        f'<td valign="top"><div style="font-size:17px;font-weight:700;line-height:1.35;color:#101828">{_e(p.get("title"))}</div><div style="margin-top:6px">{" ".join(tags)}</div></td>'
        "</tr></table>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px"><tr>'
        '<td width="50%" valign="top" style="background:#fff5f4;border:1px solid #fecdca;border-radius:8px;padding:12px">'
        '<div style="font-size:12px;font-weight:800;letter-spacing:.6px;color:#b42318">⚠ THE PROBLEM</div>'
        f'<div style="margin-top:5px;font-size:14px;line-height:1.5;color:#1d2939">{_e(p.get("problem_summary") or p.get("why_important_today"))}</div>'
        f'{_quote("People ask:", p.get("people_asked"), "#b42318")}{problem_extra}'
        f'<div style="{label_css}">SEEN AT</div><div style="margin-top:4px">{seen}</div>'
        '</td><td width="12"></td>'
        '<td width="50%" valign="top" style="background:#f3fbf6;border:1px solid #abefc6;border-radius:8px;padding:12px">'
        '<div style="font-size:12px;font-weight:800;letter-spacing:.6px;color:#067647">✔ THE SOLUTION</div>'
        f'<div style="margin-top:5px;font-size:14px;line-height:1.5;color:#1d2939">{fix_text}</div>'
        f'{_quote("What worked (community):", p.get("community_answer"), "#067647")}'
        f"{steps_html}{code}{solution_extra}"
        f'<div style="{label_css}">SOLUTION FOUND AT</div><div style="margin-top:4px">{found}</div>'
        "</td></tr></table></td></tr></table>"
    )


def render(report: dict[str, Any], page_url: str = "") -> str:
    problems = report.get("problems") or []
    counts: dict[str, int] = {}
    for p in problems:
        counts[str(p.get("severity"))] = counts.get(str(p.get("severity")), 0) + 1
    community = sum(1 for p in problems if p.get("people_asked") or p.get("community_answer"))
    slot = str(report.get("slot", "")).title()

    stats = "".join(
        f'<td align="center" style="padding:0 10px 0 0"><div style="font-size:22px;font-weight:800;color:#ffffff">{n}</div>'
        f'<div style="font-size:11px;color:#c7d7fe;letter-spacing:.4px">{label}</div></td>'
        for n, label in [(len(problems), "PROBLEMS"), (counts.get("critical", 0) + counts.get("high", 0), "HIGH / CRITICAL"), (community, "FROM COMMUNITY")]
    )
    bar_colors = {"critical": "#f04438", "high": "#fb6514", "medium": "#fdb022", "low": "#53b1fd"}
    segments = "".join(
        f'<td width="{max(4, round(100 * counts[k] / len(problems)))}%" style="background:{c};height:8px;font-size:1px;line-height:8px">&nbsp;</td>'
        for k, c in bar_colors.items()
        if counts.get(k) and problems
    )
    bar = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="2" style="margin-top:14px"><tr>{segments}</tr></table>'
        '<div style="margin-top:4px;font-size:11px;color:#c7d7fe">severity mix: '
        + ", ".join(f"{counts[k]} {k}" for k in bar_colors if counts.get(k))
        + "</div>"
        if segments else ""
    )
    urgent = [p for p in problems if str(p.get("severity")) in ("critical", "high")] or problems[:3]
    todo_rows = "".join(
        f'<tr><td width="26" valign="top" style="font-size:18px;line-height:22px;color:#3b5bdb">☐</td>'
        f'<td style="padding:2px 0 8px;font-size:14px;line-height:1.5;color:#1d2939"><b>{_e(p.get("title"))}</b><br>'
        f'<span style="color:#475467">{_e((p.get("recommended_action") or {}).get("summary"))}</span></td></tr>'
        for p in urgent
        if (p.get("recommended_action") or {}).get("summary")
    )
    todo = (
        '<tr><td style="background:#eef4ff;padding:16px 24px;border-bottom:1px solid #d1e0ff">'
        '<div style="font-size:12px;font-weight:800;letter-spacing:.8px;color:#3b5bdb;margin-bottom:8px">✅ DO TODAY</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{todo_rows}</table></td></tr>'
        if todo_rows else ""
    )
    rows = []
    for p in problems:
        fg, bg, name = _sev(p)
        rows.append(
            "<tr>"
            f'<td width="30" style="padding:6px 0;font-weight:700;color:#667085;font-size:13px">{_e(p.get("rank"))}</td>'
            f'<td style="padding:6px 8px 6px 0;font-size:14px;color:#101828">{_e(p.get("title"))}</td>'
            f'<td align="right" style="padding:6px 0;white-space:nowrap">{_pill(name, fg, bg)}</td></tr>'
        )
    parts = [
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#eef2f6">',
        f'<div style="display:none;max-height:0;overflow:hidden;color:#eef2f6">{_e(report.get("daily_summary"))}</div>',
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f6"><tr><td align="center" style="padding:20px 10px;font-family:{FONT}">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:760px">',
        '<tr><td style="background:#1d2b64;background-image:linear-gradient(135deg,#1d2b64,#3b5bdb);border-radius:12px 12px 0 0;padding:22px 24px">'
        f'<div style="font-size:12px;font-weight:700;letter-spacing:1.4px;color:#c7d7fe">◎ PROBLEM RADAR · {_e(slot.upper())} EDITION</div>'
        f'<div style="font-size:26px;font-weight:800;color:#ffffff;margin-top:4px">{_e(report.get("report_date"))}</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px"><tr>{stats}</tr></table>{bar}</td></tr>',
        f'<tr><td style="background:#ffffff;padding:18px 24px;border-bottom:1px solid #eaecf0;font-size:15px;line-height:1.6;color:#1d2939">{_e(report.get("daily_summary")) or "No summary for this run."}</td></tr>',
        todo,
        '<tr><td style="background:#f8fafc;padding:18px 20px 4px">',
    ]
    if problems:
        parts.append(
            '<div style="font-size:12px;font-weight:800;letter-spacing:.8px;color:#667085;margin-bottom:6px">AT A GLANCE</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:18px;background:#ffffff;border:1px solid #eaecf0;border-radius:8px"><tr><td style="padding:6px 14px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table></td></tr></table>'
        )
        parts += [_card(p) for p in problems]
    else:
        parts.append('<p style="color:#475467;font-size:14px">No meaningful problems were found in this run.</p>')
    watch = [w for w in (report.get("low_priority_watchlist") or []) if isinstance(w, dict) and w.get("title")][:5]
    if watch:
        items = "".join(f'<li style="margin:0 0 4px"><b>{_e(w.get("title"))}</b> — {_e(w.get("reason_to_watch"))}</li>' for w in watch)
        parts.append(
            '<div style="font-size:12px;font-weight:800;letter-spacing:.8px;color:#667085;margin:6px 0">WORTH WATCHING</div>'
            f'<ul style="margin:0 0 14px 18px;padding:0;font-size:13px;color:#344054">{items}</ul>'
        )
    focus = [str(t) for t in (report.get("tomorrows_focus") or []) if t][:6]
    if focus:
        chips = " ".join(_pill(t, "#344054", "#e4e7ec") for t in focus)
        parts.append(f'<div style="font-size:12px;font-weight:800;letter-spacing:.8px;color:#667085;margin:6px 0">NEXT FOCUS</div><div style="margin:0 0 14px;line-height:2">{chips}</div>')
    parts.append("</td></tr>")
    if page_url.startswith(("https://", "http://")):
        parts.append(
            f'<tr><td align="center" style="background:#f8fafc;padding:6px 0 20px"><a href="{_e(page_url)}" style="display:inline-block;background:#3b5bdb;color:#ffffff;font-weight:700;font-size:14px;text-decoration:none;padding:11px 22px;border-radius:8px">Open the full report in Team Pulse →</a></td></tr>'
        )
    parts.append(
        '<tr><td style="background:#101828;border-radius:0 0 12px 12px;padding:14px 24px;font-size:11px;line-height:1.5;color:#98a2b3">'
        "Researched by Claude from public web sources: official docs, GitHub, Stack Overflow, Reddit, X, Hacker News, Medium and more. "
        "Community answers are reports from people, not guarantees. Check a fix before using it in production.</td></tr>"
        "</table></td></tr></table></body></html>"
    )
    return "".join(parts)


class Mailer:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, sender: str, recipients: list[str], http: httpx.AsyncClient, page_url: str = "") -> None:
        self.sender, self.recipients, self.http, self.page_url = sender, recipients, http, page_url
        self._creds = (tenant_id, client_id, client_secret)
        self._app: msal.ConfidentialClientApplication | None = None  # made on first use: it contacts Microsoft, which must never block start-up

    @property
    def configured(self) -> bool:
        return bool(self.sender and self.recipients)

    async def _token(self) -> str:
        def acquire() -> dict[str, Any]:
            if self._app is None:
                tenant, client, secret = self._creds
                self._app = msal.ConfidentialClientApplication(client, client_credential=secret, authority=f"https://login.microsoftonline.com/{tenant}")
            return self._app.acquire_token_for_client(["https://graph.microsoft.com/.default"])

        result = await asyncio.to_thread(acquire)
        if "access_token" not in result:
            raise RuntimeError(str(result.get("error_description") or result.get("error") or "no token")[:300])
        return result["access_token"]

    async def send(self, report: dict[str, Any]) -> None:
        await self.send_message(subject(report), render(report, self.page_url), self.recipients)

    async def send_message(self, subject_line: str, html_body: str, recipients: list[str]) -> None:
        """Send any HTML email from the configured mailbox (used for Claude account notices)."""
        body = {
            "message": {
                "subject": subject_line,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
            },
            "saveToSentItems": True,
        }
        res = await self.http.post(
            f"https://graph.microsoft.com/v1.0/users/{self.sender}/sendMail", json=body, headers={"Authorization": f"Bearer {await self._token()}"}
        )
        if res.status_code >= 400:
            why = ""
            try:
                why = (res.json().get("error") or {}).get("message", "")
            except ValueError:
                pass
            if res.status_code == 403:
                raise RuntimeError("Microsoft refused to send: an admin must approve the Mail.Send permission for Team Pulse. " + why[:160])
            raise RuntimeError(f"Microsoft could not send the email (HTTP {res.status_code}). {why[:160]}")
        logger.info("Email sent to %d people: %s", len(recipients), subject_line[:60])
