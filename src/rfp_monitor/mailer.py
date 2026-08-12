from __future__ import annotations

import os
import smtplib
from collections.abc import Sequence
from email.message import EmailMessage
from html import escape

from .config import NotificationConfig
from .emma_email import EmmaNotice
from .models import MatchResult


def _truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _deliver(message: EmailMessage) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if _truthy(os.getenv("SMTP_STARTTLS")):
            smtp.starttls()
        username = os.getenv("SMTP_USERNAME")
        password = os.getenv("SMTP_PASSWORD")
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


def _sender(recipients: Sequence[str]) -> str:
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    if not host or not sender or not recipients:
        raise ValueError(
            "Email delivery requires SMTP_HOST, SMTP_FROM, and at least one recipient"
        )
    return sender


def send_digest(
    notifications: NotificationConfig,
    subject: str,
    text: str,
    html: str,
) -> None:
    sender = _sender(notifications.recipients)

    message = EmailMessage()
    message["Subject"] = f"{notifications.subject_prefix} {subject}".strip()
    message["From"] = sender
    message["To"] = ", ".join(notifications.recipients)
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    _deliver(message)


def render_forward_text(notice: EmmaNotice, match: MatchResult, change_kind: str) -> str:
    matched = "\n".join(
        f"  {group}: {', '.join(terms)}" for group, terms in sorted(match.matched.items())
    )
    rows = [
        ("RFx name", notice.rfx_name),
        ("BPM ID", notice.bpm_id),
        ("Main commodity", notice.commodity),
        ("Lot #", notice.lot),
        ("Round #", notice.round_number),
        ("End date", notice.end_date),
        ("Requester", notice.requester),
    ]
    lines = [
        f"{match.classification} (score {match.score}) — {change_kind}",
        "",
        f"Why: {match.reason}",
        "",
        "Matched signals:",
        matched or "  (none)",
        "",
        "Solicitation",
        "------------",
    ]
    lines.extend(f"{label}: {value}" for label, value in rows if value)
    lines.extend(
        (
            "",
            f"Link: {notice.link or 'not present in the notification'}",
            "",
            "The original eMMA notification is attached as .eml.",
        )
    )
    return "\n".join(lines) + "\n"


def render_forward_html(notice: EmmaNotice, match: MatchResult, change_kind: str) -> str:
    rows = [
        ("RFx name", notice.rfx_name),
        ("BPM ID", notice.bpm_id),
        ("Main commodity", notice.commodity),
        ("Lot #", notice.lot),
        ("Round #", notice.round_number),
        ("End date", notice.end_date),
        ("Requester", notice.requester),
    ]
    cells = "".join(
        f"<tr><td style=\"padding:2px 12px 2px 0;color:#627d98\">{escape(label)}</td>"
        f"<td style=\"padding:2px 0\"><strong>{escape(value)}</strong></td></tr>"
        for label, value in rows
        if value
    )
    matched = "".join(
        f"<li>{escape(group)}: {escape(', '.join(terms))}</li>"
        for group, terms in sorted(match.matched.items())
    )
    link = escape(notice.link, quote=True) if notice.link else ""
    link_html = (
        f"<p><a href=\"{link}\">Open the solicitation in eMMA</a></p>"
        if link
        else "<p>No solicitation link was present in the notification.</p>"
    )
    return (
        "<!doctype html><html><body style=\"font-family:Arial,sans-serif;color:#102a43\">"
        f"<h1 style=\"font-size:20px;margin:0 0 4px\">{escape(notice.rfx_name)}</h1>"
        f"<p style=\"margin:0 0 14px;color:#627d98\">{escape(match.classification or '')}"
        f" · score {match.score} · {escape(change_kind)}</p>"
        f"<p style=\"margin:0 0 14px\"><strong>Why:</strong> {escape(match.reason)}</p>"
        f"<table style=\"border-collapse:collapse;font-size:14px\">{cells}</table>"
        f"{link_html}"
        f"<p style=\"margin:14px 0 4px\"><strong>Matched signals</strong></p><ul>{matched}</ul>"
        "<p style=\"color:#627d98;font-size:13px\">The original eMMA notification is "
        "attached as .eml.</p></body></html>"
    )


def build_forward(
    recipients: Sequence[str],
    notice: EmmaNotice,
    match: MatchResult,
    change_kind: str,
    original: EmailMessage,
    *,
    subject_prefix: str = "[RFP Monitor]",
    sender: str = "preview@localhost",
) -> EmailMessage:
    """Build the forward message, attaching the untouched original notification."""

    message = EmailMessage()
    message["Subject"] = (
        f"{subject_prefix} {match.classification}: {notice.rfx_name}".strip()
    )
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if notice.message_id:
        message["References"] = notice.message_id
    message.set_content(render_forward_text(notice, match, change_kind))
    message.add_alternative(render_forward_html(notice, match, change_kind), subtype="html")

    filename = f"emma-{notice.bpm_id or 'notice'}.eml"
    message.add_attachment(original, filename=filename)
    return message


def forward_notice(
    recipients: Sequence[str],
    notice: EmmaNotice,
    match: MatchResult,
    change_kind: str,
    original: EmailMessage,
    *,
    subject_prefix: str = "[RFP Monitor]",
) -> EmailMessage:
    """Forward one matched eMMA notice over SMTP."""

    message = build_forward(
        recipients,
        notice,
        match,
        change_kind,
        original,
        subject_prefix=subject_prefix,
        sender=_sender(recipients),
    )
    _deliver(message)
    return message
