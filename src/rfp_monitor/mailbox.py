"""Read-only IMAP access to the state-portal registration inbox."""

from __future__ import annotations

import imaplib
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_LITERAL = re.compile(rb"\{(\d+)\}$")


class MailboxError(RuntimeError):
    """The mailbox could not be reached or read."""


def imap_date(value: date) -> str:
    return f"{value.day:02d}-{_MONTHS[value.month - 1]}-{value.year}"


def _search_criteria(senders: Sequence[str], since: date | None) -> list[str]:
    criteria: list[str] = []
    if since:
        criteria.extend(["SINCE", imap_date(since)])
    clean = [sender for sender in senders if sender]
    if clean:
        # IMAP SEARCH has no IN operator; N senders need N-1 prefix ORs.
        terms: list[str] = []
        for sender in clean:
            terms.extend(["FROM", f'"{sender}"'])
        criteria.extend(["OR"] * (len(clean) - 1) + terms)
    return criteria or ["ALL"]


@contextmanager
def imap_connection(folder: str = "INBOX") -> Iterator[imaplib.IMAP4_SSL]:
    host = os.getenv("IMAP_HOST")
    username = os.getenv("IMAP_USERNAME")
    password = os.getenv("IMAP_PASSWORD")
    if not host or not username or not password:
        raise MailboxError(
            "Mailbox access requires IMAP_HOST, IMAP_USERNAME, and IMAP_PASSWORD"
        )
    port = int(os.getenv("IMAP_PORT", "993"))

    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=30)
    except OSError as exc:
        raise MailboxError(f"could not connect to {host}:{port}: {exc}") from exc
    try:
        client.login(username, password)
        # Read-only: the monitor must never change flags in the user's mailbox.
        status, _ = client.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise MailboxError(f"could not open folder {folder!r}")
        yield client
    except imaplib.IMAP4.error as exc:
        raise MailboxError(f"IMAP error: {exc}") from exc
    finally:
        try:
            client.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def fetch_raw_messages(
    *,
    folder: str = "INBOX",
    senders: Sequence[str] = (),
    since_days: int | None = 7,
    limit: int = 200,
) -> list[bytes]:
    """Return raw messages from the inbox, newest last, without altering flags."""

    since = (
        (datetime.now(UTC).date() - timedelta(days=since_days))
        if since_days is not None
        else None
    )
    with imap_connection(folder) as client:
        status, data = client.search(None, *_search_criteria(senders, since))
        if status != "OK":
            raise MailboxError("IMAP search failed")
        ids = (data[0] or b"").split()
        if limit and len(ids) > limit:
            ids = ids[-limit:]

        messages: list[bytes] = []
        for message_id in ids:
            # PEEK keeps the message unread for the human reading the same inbox.
            status, payload = client.fetch(message_id, "(BODY.PEEK[])")
            if status != "OK" or not payload:
                continue
            for part in payload:
                if isinstance(part, tuple) and len(part) > 1 and _LITERAL.search(part[0] or b""):
                    messages.append(part[1])
                    break
    return messages


def read_eml_directory(path: str | Path) -> list[bytes]:
    """Read saved .eml files, so the pipeline can be exercised without a mailbox."""

    directory = Path(path)
    if not directory.is_dir():
        raise MailboxError(f"not a directory: {directory}")
    return [item.read_bytes() for item in sorted(directory.glob("*.eml"))]
