"""Parse eMaryland Marketplace Advantage (eMMA) solicitation notification email."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from .models import Opportunity

SOURCE_ID = "maryland-emma-email"
SOURCE_NAME = "Maryland eMMA (email)"
SOURCE_STATE = "MD"
BROWSE_URL = "https://emma.maryland.gov/page.aspx/en/rfp/request_browse_public"

DEFAULT_SENDERS = ("no-reply.emma@maryland.gov",)

_SUBJECT_RE = re.compile(
    r"new\s*/\s*updated\s+solicitation\s*:\s*(?P<name>.+)\s*",
    re.IGNORECASE | re.DOTALL,
)
_LABELS = (
    "RFx name",
    "BPM ID",
    "Main commodity",
    "Lot #",
    "Round #",
    "End date",
    "Requester",
)
_LABEL_RE = re.compile(
    r"(?P<label>" + "|".join(re.escape(label) for label in _LABELS) + r")\s*:[^\S\n]*",
    re.IGNORECASE,
)
_BLOCK_TAGS = frozenset(
    {
        "br", "p", "div", "tr", "td", "th", "li", "ul", "ol",
        "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    }
)
_UNKNOWN = frozenset({"", "undefined", "n/a", "none", "other"})


class EmmaEmailError(ValueError):
    """The message is not a parseable eMMA solicitation notice."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._suppress = 0
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self._suppress += 1
            return
        if tag in _BLOCK_TAGS:
            self.chunks.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"}:
            self._suppress = max(0, self._suppress - 1)
            return
        if tag in _BLOCK_TAGS:
            self.chunks.append("\n")
        if tag == "a":
            if self._href:
                self.links.append(("".join(self._anchor).strip(), self._href.strip()))
            self._href = None
            self._anchor = []

    def handle_data(self, data: str) -> None:
        if self._suppress:
            return
        self.chunks.append(data)
        if self._href is not None:
            self._anchor.append(data)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


def _normalize(text: str) -> str:
    text = re.sub(r"[^\S\n]+", " ", text.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _body(message: EmailMessage) -> tuple[str, list[tuple[str, str]]]:
    """Return normalized body text and any anchors, preferring the HTML alternative.

    HTML is preferred because eMMA keeps each labelled field on its own line there,
    while the text/plain alternative may soft-wrap long RFx names onto a second line.
    """

    html_part = plain_part = None
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        subtype = part.get_content_subtype()
        if subtype == "html" and html_part is None:
            html_part = part
        elif subtype == "plain" and plain_part is None:
            plain_part = part

    for part in (html_part, plain_part):
        if part is None:
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if part is html_part:
            extractor = _TextExtractor()
            extractor.feed(content)
            extractor.close()
            return _normalize(extractor.text), extractor.links
        return _normalize(content), []
    return "", []


def _fields(text: str) -> dict[str, str]:
    matches = list(_LABEL_RE.finditer(text))
    found: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        line_end = text.find("\n", start)
        if line_end != -1:
            stop = min(stop, line_end)
        label = match.group("label").lower()
        value = text[start:stop].strip()
        if value and label not in found:
            found[label] = value
    return found


def _solicitation_link(links: list[tuple[str, str]]) -> str:
    for label, href in links:
        if label.strip().lower() == "link" and href.lower().startswith(("http://", "https://")):
            return href
    for _, href in links:
        if "emma.maryland.gov" in href.lower():
            return href
    return ""


def _is_known_sender(message: EmailMessage, allowed: tuple[str, ...]) -> bool:
    addresses = [
        address.lower()
        for _, address in getaddresses(
            [str(value) for value in message.get_all("from", [])]
        )
        if address
    ]
    wanted = {value.lower() for value in allowed}
    domains = {value.split("@", 1)[-1] for value in wanted if "@" in value}
    return any(
        address in wanted or address.split("@", 1)[-1] in domains for address in addresses
    )


@dataclass(frozen=True)
class EmmaNotice:
    """One parsed eMMA solicitation invitation."""

    rfx_name: str
    bpm_id: str = ""
    commodity: str = ""
    lot: str = ""
    round_number: str = ""
    end_date: str = ""
    requester: str = ""
    link: str = ""
    message_id: str = ""
    subject: str = ""
    received_at: str = ""

    def to_opportunity(self) -> Opportunity:
        # browse_url carries the per-round link: Opportunity excludes it from both
        # the fingerprint and searchable_text, so a link token that rotates per send
        # cannot trigger a duplicate alert, and a requester's name (which may contain
        # a leadership title such as "Dean") cannot inject a false keyword match.
        commodity = "" if self.commodity.strip().lower() in _UNKNOWN else self.commodity
        return Opportunity(
            source_id=SOURCE_ID,
            source_name=SOURCE_NAME,
            state=SOURCE_STATE,
            browse_url=self.link or BROWSE_URL,
            title=self.rfx_name,
            external_id=self.bpm_id,
            detail_url="",
            status=f"Round {self.round_number}" if self.round_number else "",
            due_date=self.end_date,
            published_date="",
            category=commodity,
            agency="",
            solicitation_type="",
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def parse_emma_email(
    raw: bytes | EmailMessage,
    *,
    allowed_senders: tuple[str, ...] = DEFAULT_SENDERS,
) -> EmmaNotice:
    """Parse one eMMA notification into an EmmaNotice.

    Raises EmmaEmailError when the message is not an eMMA solicitation invitation.
    """

    message = (
        raw
        if isinstance(raw, EmailMessage)
        else BytesParser(policy=policy.default).parsebytes(raw)
    )

    if allowed_senders and not _is_known_sender(message, allowed_senders):
        raise EmmaEmailError(f"unexpected sender: {message.get('From', '')!r}")

    subject = " ".join(str(message.get("Subject", "")).split())
    subject_match = _SUBJECT_RE.search(subject)
    if not subject_match:
        raise EmmaEmailError(f"subject is not a solicitation notice: {subject!r}")

    text, links = _body(message)
    fields = _fields(text)

    rfx_name = fields.get("rfx name") or subject_match.group("name").strip()
    if not rfx_name:
        raise EmmaEmailError("no RFx name in subject or body")

    received_at = ""
    if message.get("Date"):
        try:
            received_at = parsedate_to_datetime(str(message["Date"])).isoformat()
        except (TypeError, ValueError):
            received_at = ""

    return EmmaNotice(
        rfx_name=rfx_name,
        bpm_id=fields.get("bpm id", ""),
        commodity=fields.get("main commodity", ""),
        lot=fields.get("lot #", ""),
        round_number=fields.get("round #", ""),
        end_date=fields.get("end date", ""),
        requester=fields.get("requester", ""),
        link=_solicitation_link(links),
        message_id=str(message.get("Message-ID", "")).strip(),
        subject=subject,
        received_at=received_at,
    )
