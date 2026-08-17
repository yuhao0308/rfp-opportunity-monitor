"""Read a procurement portal's notification email using its config entry.

Nothing here knows about any particular portal. What a notice looks like — who
sends it, how its subject reads, and which label precedes each field — comes
from an [[email_sources]] entry, so adding a portal is a config change.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from .config import EmailSourceConfig
from .models import Opportunity

_BLOCK_TAGS = frozenset(
    {
        "br", "p", "div", "tr", "td", "th", "li", "ul", "ol",
        "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
    }
)


class NoticeEmailError(ValueError):
    """The message is not a notice from the given source."""


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
    """Normalized body text and anchors, preferring HTML.

    HTML is preferred because portals keep each labelled field on its own line
    there, while the text/plain alternative may soft-wrap a long value.
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


def _labelled_values(text: str, labels: tuple[str, ...]) -> dict[str, str]:
    """Pull "Label: value" pairs, each value ending at the next label or line end."""

    if not labels:
        return {}
    pattern = re.compile(
        r"(?P<label>" + "|".join(re.escape(label) for label in labels) + r")\s*:[^\S\n]*",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
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


def _pick_link(links: list[tuple[str, str]], source: EmailSourceConfig) -> str:
    wanted = source.link_text.strip().lower()
    for label, href in links:
        if (
            wanted
            and label.strip().lower() == wanted
            and href.lower().startswith(("http://", "https://"))
        ):
            return href
    for _, href in links:
        if any(host.lower() in href.lower() for host in source.link_hosts):
            return href
    return ""


def sender_matches(message: EmailMessage, source: EmailSourceConfig) -> bool:
    addresses = [
        address.lower()
        for _, address in getaddresses([str(v) for v in message.get_all("from", [])])
        if address
    ]
    allowed = {value.lower() for value in source.senders}
    domains = {value.lower().lstrip("@") for value in source.sender_domains}
    return any(
        address in allowed or address.split("@", 1)[-1] in domains for address in addresses
    )


@dataclass(frozen=True)
class Notice:
    """One parsed solicitation invitation, from any configured source."""

    source_id: str
    source_name: str
    state: str
    title: str
    external_id: str = ""
    category: str = ""
    lot: str = ""
    round_number: str = ""
    due_date: str = ""
    requester: str = ""
    link: str = ""
    browse_url: str = ""
    message_id: str = ""
    subject: str = ""
    received_at: str = ""
    # Category values that carry no signal, e.g. "Other". Kept on the notice so
    # the parse stays faithful to the email and the forward still shows what the
    # portal actually said; the value is dropped only on the way to matching.
    uninformative: tuple[str, ...] = ()

    def to_opportunity(self) -> Opportunity:
        # browse_url carries the per-round link: Opportunity excludes it from both
        # the fingerprint and searchable_text, so a link token that rotates per
        # send cannot trigger a duplicate alert. The requester is left out of
        # matching entirely because it is a person, and a name such as "Dean"
        # would otherwise register as a leadership signal.
        blank = {value.strip().lower() for value in self.uninformative}
        category = "" if self.category.strip().lower() in blank else self.category
        return Opportunity(
            source_id=self.source_id,
            source_name=self.source_name,
            state=self.state,
            browse_url=self.link or self.browse_url,
            title=self.title,
            external_id=self.external_id,
            detail_url="",
            status=f"Round {self.round_number}" if self.round_number else "",
            due_date=self.due_date,
            published_date="",
            category=category,
            agency="",
            solicitation_type="",
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def parse_notice(raw: bytes | EmailMessage, source: EmailSourceConfig) -> Notice:
    """Parse one message as a notice from `source`.

    Raises NoticeEmailError when the message is not one of that source's notices.
    """

    message = (
        raw
        if isinstance(raw, EmailMessage)
        else BytesParser(policy=policy.default).parsebytes(raw)
    )

    if not sender_matches(message, source):
        raise NoticeEmailError(f"unexpected sender: {message.get('From', '')!r}")

    subject = " ".join(str(message.get("Subject", "")).split())
    subject_match = re.compile(source.subject_pattern, re.IGNORECASE).search(subject)
    if not subject_match:
        raise NoticeEmailError(f"subject does not match {source.id}: {subject!r}")

    text, links = _body(message)
    labels = tuple(source.fields.values())
    values = _labelled_values(text, labels)
    # Map the configured label back to the notice field it fills.
    by_field = {
        name: values.get(label.lower(), "") for name, label in source.fields.items()
    }

    title = by_field.get("title", "")
    if not title and "title" in subject_match.groupindex:
        title = (subject_match.group("title") or "").strip()
    if not title:
        raise NoticeEmailError("no title in the subject or the body")

    received_at = ""
    if message.get("Date"):
        try:
            received_at = parsedate_to_datetime(str(message["Date"])).isoformat()
        except (TypeError, ValueError):
            received_at = ""

    return Notice(
        source_id=source.id,
        source_name=source.name,
        state=source.state,
        title=title,
        external_id=by_field.get("external_id", ""),
        category=by_field.get("category", ""),
        lot=by_field.get("lot", ""),
        round_number=by_field.get("round_number", ""),
        due_date=by_field.get("due_date", ""),
        requester=by_field.get("requester", ""),
        link=_pick_link(links, source),
        browse_url=source.browse_url,
        message_id=str(message.get("Message-ID", "")).strip(),
        subject=subject,
        received_at=received_at,
        uninformative=source.uninformative,
    )


def parse_any(
    raw: bytes | EmailMessage, sources: tuple[EmailSourceConfig, ...]
) -> tuple[Notice, EmailSourceConfig]:
    """Parse against whichever configured source recognises the message."""

    message = (
        raw
        if isinstance(raw, EmailMessage)
        else BytesParser(policy=policy.default).parsebytes(raw)
    )
    reasons: list[str] = []
    for source in sources:
        try:
            return parse_notice(message, source), source
        except NoticeEmailError as exc:
            reasons.append(f"{source.id}: {exc}")
    raise NoticeEmailError("; ".join(reasons) or "no email sources configured")
