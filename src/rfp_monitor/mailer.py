from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from .config import NotificationConfig


def _truthy(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def send_digest(
    notifications: NotificationConfig,
    subject: str,
    text: str,
    html: str,
) -> None:
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    recipients = notifications.recipients
    if not host or not sender or not recipients:
        raise ValueError(
            "Email delivery requires SMTP_HOST, SMTP_FROM, and notifications.recipients"
        )

    message = EmailMessage()
    message["Subject"] = f"{notifications.subject_prefix} {subject}".strip()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if _truthy(os.getenv("SMTP_STARTTLS")):
            smtp.starttls()
        username = os.getenv("SMTP_USERNAME")
        password = os.getenv("SMTP_PASSWORD")
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)
