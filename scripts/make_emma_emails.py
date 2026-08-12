#!/usr/bin/env python3
"""Generate eMMA solicitation notification emails from portal records.

The real notices arrive rarely, so test material is built here instead: each
record mirrors what eMMA shows on a solicitation's detail page, and this script
renders it into the same multipart/alternative notice the portal mails out.

    python scripts/make_emma_emails.py examples/emma_solicitations.json \
        --out tests/fixtures/emails/generated

Every generated file is parsed back with rfp_monitor.emma_email before it is
written, so a record that would produce an unparseable notice fails loudly here
rather than silently weakening a test.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rfp_monitor.emma_email import parse_emma_email

SENDER = "eMaryland Marketplace Advantage (eMMA) <no-reply.emma@maryland.gov>"
DEFAULT_RECIPIENT = "WANG Yuhao <ks973111@gmail.com>"
DEFAULT_GREETING_NAME = "Yuhao Wang"
VIEW_URL = "https://emma.maryland.gov/page.aspx/en/rfp/request_view"
NOTIFICATIONS_URL = "https://emma.maryland.gov/page.aspx/en/usr/notifications"
EASTERN = timezone(timedelta(hours=-4))  # eMMA stamps its mail in local time.

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ),
        start=1,
    )
}
_BPM_RE = re.compile(r"(\d+)\s*$")

INTRO = (
    "You are invited to respond to the solicitation listed below. It's "
    "important that you read all documents within the solicitation thoroughly."
)
ROUND_NOTE = (
    "If this invitation pertains to Round 2 or greater of the solicitation, "
    "you MUST advance your response from the previous round (update if "
    "necessary) and resubmit. A new web link is created and shared via email "
    "each time a solicitation round is updated. Be sure to update all "
    "associated bookmarks to ensure you're accessing the most current round of "
    "the solicitation."
)
CLOSING = (
    "We look forward to hearing from you. Please click the following link to "
    "review this solicitation and proceed with next steps:"
)
DISCLAIMER = (
    "Disclaimer: Neither this email nor the process it supports in any way "
    "guarantees the award of a solicitation or offers any contractual "
    "agreement between a prospective vendor and the State of Maryland. All "
    "vendor setup, vetting, and approval is conducted at the sole discretion "
    "of authorized representatives of the State of Maryland and may be "
    "discontinued based on the representative's investigative activities "
    "within the boundaries of the Maryland code of regulations (COMAR) for "
    "vendor management."
)
AUTO_NOTE = "This is an automatically generated e-mail, please do not reply"


class RecordError(ValueError):
    """A record cannot be rendered into a notice."""


def _numeric_id(value: str) -> str:
    """Return the bare digits eMMA prints in mail: "BPM058205" -> "58205"."""

    match = _BPM_RE.search(str(value).strip())
    if not match:
        raise RecordError(f"no numeric BPM id in {value!r}")
    return match.group(1).lstrip("0") or "0"


def _end_date(value: str) -> str:
    """Normalize a portal due date to the M/D/YYYY the mail uses.

    Accepts "Aug 17 2026  5:00PM", "8/17/2026", and ISO "2026-08-17"; the time
    of day is dropped because the notice carries only the calendar date.
    """

    text = " ".join(str(value).split())
    if not text:
        raise RecordError("due date is required")

    slash = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if slash:
        month, day, year = (int(part) for part in slash.groups())
        return f"{month}/{day}/{year}"

    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
        return f"{month}/{day}/{year}"

    named = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if named:
        month = _MONTHS.get(named.group(1)[:3].lower())
        if month:
            return f"{month}/{int(named.group(2))}/{int(named.group(3))}"

    raise RecordError(f"unrecognized due date: {value!r}")


def _sent_at(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RecordError(f"unrecognized sent_at: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=EASTERN)


def _slug(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words[:6]) or "solicitation"


@dataclass(frozen=True)
class Notice:
    """One solicitation, in the shape eMMA's detail page presents it."""

    title: str
    bpm_id: str
    due_date: str
    round_number: str = "1"
    main_category: str = "Other"
    lot: str = "UNDEFINED"
    buyer: str = ""
    sent_at: datetime = field(default_factory=lambda: datetime.now(EASTERN))
    recipient: str = DEFAULT_RECIPIENT
    greeting_name: str = DEFAULT_GREETING_NAME
    # Carried through for realism/other assertions; absent from the mailed notice.
    alternate_id: str = ""
    status: str = ""
    solicitation_type: str = ""
    issuing_agency: str = ""
    buyer_email: str = ""
    summary: str = ""

    @classmethod
    def from_record(cls, record: dict, *, default_sent_at: datetime) -> Notice:
        try:
            title = " ".join(str(record["title"]).split())
            bpm_id = _numeric_id(record["id"])
            due_date = _end_date(record["due_date"])
        except KeyError as exc:
            raise RecordError(f"missing required field: {exc.args[0]}") from exc
        if not title:
            raise RecordError("title is required")

        lot = record.get("lot")
        if lot is None:
            # eMMA mails "UNDEFINED" unless the solicitation is split into lots;
            # a functional area of 1 is the single-lot default, not a real lot.
            area = str(record.get("functional_area", "")).strip()
            lot = area if area and area != "1" else "UNDEFINED"

        return cls(
            title=title,
            bpm_id=bpm_id,
            due_date=due_date,
            round_number=str(record.get("round", "1")).strip() or "1",
            main_category=str(record.get("main_category", "Other")).strip() or "Other",
            lot=str(lot),
            buyer=" ".join(str(record.get("buyer", "")).split()),
            sent_at=_sent_at(record.get("sent_at"), default_sent_at),
            recipient=str(record.get("recipient", DEFAULT_RECIPIENT)),
            greeting_name=str(record.get("greeting_name", DEFAULT_GREETING_NAME)),
            alternate_id=str(record.get("alternate_id", "")),
            status=str(record.get("status", "")),
            solicitation_type=str(record.get("solicitation_type", "")),
            issuing_agency=str(record.get("issuing_agency", "")),
            buyer_email=str(record.get("buyer_email", "")),
            summary=str(record.get("summary", "")),
        )

    @property
    def subject(self) -> str:
        return f"New / Updated Solicitation:  {self.title}"

    @property
    def link(self) -> str:
        # The token rotates per round, matching the per-round links eMMA sends.
        return (
            f"{VIEW_URL}/{self.bpm_id}"
            f"?round={self.round_number}&token=t{self.bpm_id}{self.round_number}"
        )

    @property
    def message_id(self) -> str:
        return f"<emma-{self.bpm_id}-r{self.round_number}@maryland.gov>"

    @property
    def filename(self) -> str:
        return f"{self.bpm_id}-r{self.round_number}-{_slug(self.title)}.eml"

    @property
    def fields(self) -> tuple[tuple[str, str], ...]:
        return (
            ("RFx name", self.title),
            ("BPM ID", self.bpm_id),
            ("Main commodity", self.main_category),
            ("Lot #", self.lot),
            ("Round #", self.round_number),
            ("End date", self.due_date),
            ("Requester", self.buyer),
        )


def _plain_body(notice: Notice) -> str:
    lines = [
        notice.subject,
        "",
        f"Dear {notice.greeting_name},",
        "",
        INTRO,
        "",
        ROUND_NOTE,
        "",
    ]
    lines += [f"{label}:  {value}" for label, value in notice.fields]
    lines += [CLOSING, notice.link, "", "Regards,", "", DISCLAIMER, "", AUTO_NOTE, ""]
    return "\n".join(lines)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _html_body(notice: Notice) -> str:
    rows = "".join(
        f"<div>{_escape(label)}:&nbsp; {_escape(value)}</div>"
        for label, value in notice.fields
    )
    return (
        "<html><body>"
        f"<p>Dear {_escape(notice.greeting_name)},</p>"
        f"<p>{_escape(INTRO)}</p>"
        f"<p>{_escape(ROUND_NOTE)}</p>"
        f"{rows}"
        f'<p>{_escape(CLOSING)} <a href="{_escape(notice.link)}">Link</a></p>'
        "<p>Regards,</p>"
        '<p><img src="cid:logo" alt="eMMA_Logo_Mobile.png"></p>'
        f"<p>{_escape(DISCLAIMER)}</p>"
        f"<p>{_escape(AUTO_NOTE)}</p>"
        f'<p><a href="{NOTIFICATIONS_URL}">Click here</a> to manage your '
        "notifications settings.</p>"
        "</body></html>"
    )


def build_message(notice: Notice) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = notice.subject
    message["From"] = SENDER
    message["Reply-To"] = "no-reply.emma@maryland.gov"
    message["To"] = notice.recipient
    message["Date"] = format_datetime(notice.sent_at)
    message["Message-ID"] = notice.message_id
    message.set_content(_plain_body(notice), subtype="plain", charset="utf-8", cte="quoted-printable")
    message.add_alternative(_html_body(notice), subtype="html", charset="utf-8", cte="quoted-printable")
    return message


def _verify(raw: bytes, notice: Notice) -> None:
    parsed = parse_emma_email(raw)
    expected = {
        "rfx_name": notice.title,
        "bpm_id": notice.bpm_id,
        "commodity": notice.main_category,
        "lot": notice.lot,
        "round_number": notice.round_number,
        "end_date": notice.due_date,
        "requester": notice.buyer,
        "link": notice.link,
    }
    mismatched = {
        name: (value, getattr(parsed, name))
        for name, value in expected.items()
        if getattr(parsed, name) != value
    }
    if mismatched:
        detail = "; ".join(
            f"{name}: expected {want!r}, parsed {got!r}"
            for name, (want, got) in mismatched.items()
        )
        raise RecordError(f"generated notice does not round-trip ({detail})")


def load_records(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("solicitations", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise RecordError(f"{path}: expected a list of records")
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", type=Path, help="JSON file of solicitation records")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "emails" / "generated",
        help="directory to write .eml files into (created if missing)",
    )
    parser.add_argument(
        "--spacing-days",
        type=float,
        default=1.0,
        help="days between generated send times when a record omits sent_at",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print what would be written, without writing",
    )
    args = parser.parse_args(argv)

    try:
        records = load_records(args.records)
    except (OSError, json.JSONDecodeError, RecordError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(EASTERN).replace(microsecond=0)
    built: list[tuple[Notice, bytes]] = []
    for index, record in enumerate(records):
        default_sent_at = now - timedelta(days=args.spacing_days * (len(records) - 1 - index))
        try:
            notice = Notice.from_record(record, default_sent_at=default_sent_at)
            raw = build_message(notice).as_bytes()
            _verify(raw, notice)
        except RecordError as exc:
            print(f"error: record {index + 1}: {exc}", file=sys.stderr)
            return 1
        built.append((notice, raw))

    if not args.dry_run:
        args.out.mkdir(parents=True, exist_ok=True)
    for notice, raw in built:
        target = args.out / notice.filename
        if not args.dry_run:
            target.write_bytes(raw)
        print(f"{'would write' if args.dry_run else 'wrote'} {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
