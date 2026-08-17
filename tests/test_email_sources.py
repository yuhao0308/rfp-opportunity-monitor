"""Adding a portal must be a config change, never a code change.

Every test here defines a fictional portal whose email looks nothing like
Maryland's — different sender, different subject, different field labels — and
parses it with the shipped parser. Nothing in src/ knows this portal exists.
"""

from email.message import EmailMessage

import pytest

from rfp_monitor.config import EmailSourceConfig, load_config
from rfp_monitor.notice_email import NoticeEmailError, parse_any, parse_notice

CONFIG = """
[monitor]
keyword_path = "config/keywords.json"
state_path = "var/test.sqlite3"

[notifications]
recipients = ["digest@example.test"]

[[sources]]
id = "maryland"
name = "Maryland eMMA"
state = "MD"
url = "https://example.test/rfps"

[[email_sources]]
id = "riverbend"
name = "Riverbend Procurement"
state = "RB"
senders = ["bids@riverbend.example"]
subject_pattern = 'bid\\s+opportunity\\s*:\\s*(?P<title>.+)'
browse_url = "https://riverbend.example/bids"
link_text = "View Solicitation"

[email_sources.fields]
title = "Opportunity Title"
external_id = "Reference Number"
due_date = "Closing Date"
requester = "Contact"
"""


def riverbend_email(
    title: str = "Executive Search Services for the Superintendent",
    sender: str = "Riverbend Bids <bids@riverbend.example>",
) -> bytes:
    message = EmailMessage()
    message["Subject"] = f"Bid Opportunity: {title}"
    message["From"] = sender
    message["To"] = "vendor@example.test"
    message["Date"] = "Mon, 10 Aug 2026 09:00:00 -0400"
    message["Message-ID"] = "<rb-2026-11@riverbend.example>"
    message.set_content("plain fallback")
    message.add_alternative(
        "<html><body>"
        f"<p>Dear Vendor,</p><div>Opportunity Title: {title}</div>"
        "<div>Reference Number: RB-2026-11</div>"
        "<div>Closing Date: 10/15/2026</div>"
        "<div>Contact: Dana Fields</div>"
        '<p>Please <a href="https://riverbend.example/bids/11">View Solicitation</a>.</p>'
        "</body></html>",
        subtype="html",
    )
    return message.as_bytes()


@pytest.fixture
def riverbend(tmp_path) -> EmailSourceConfig:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_config(path).email_sources[0]


def test_a_new_portal_parses_from_config_alone(riverbend):
    notice = parse_notice(riverbend_email(), riverbend)

    assert notice.source_id == "riverbend"
    assert notice.state == "RB"
    assert notice.title == "Executive Search Services for the Superintendent"
    assert notice.external_id == "RB-2026-11"
    assert notice.due_date == "10/15/2026"
    assert notice.requester == "Dana Fields"
    assert notice.link == "https://riverbend.example/bids/11"


def test_a_new_portal_reaches_the_shared_matcher(riverbend):
    from pathlib import Path

    from rfp_monitor.matching import Matcher, load_keyword_library

    root = Path(__file__).resolve().parents[1]
    matcher = Matcher(load_keyword_library(root / "config" / "keywords.json"))

    result = matcher.match(parse_notice(riverbend_email(), riverbend).to_opportunity())

    assert result.classification == "Strong opportunity"


def test_fields_left_out_of_the_config_are_simply_not_read(riverbend):
    notice = parse_notice(riverbend_email(), riverbend)

    assert notice.category == ""
    assert notice.lot == ""
    assert notice.round_number == ""


def test_another_portals_mail_is_rejected(riverbend):
    with pytest.raises(NoticeEmailError, match="unexpected sender"):
        parse_notice(riverbend_email(sender="someone@elsewhere.example"), riverbend)


def test_parse_any_routes_each_message_to_its_own_source(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG, encoding="utf-8")
    sources = load_config(path).email_sources

    maryland = load_config("config.toml").email_sources
    combined = (*sources, *maryland)

    notice, source = parse_any(riverbend_email(), combined)
    assert source.id == "riverbend"

    fixture = (
        __import__("pathlib").Path(__file__).parent
        / "fixtures" / "emails" / "01-educational-materials.eml"
    )
    notice, source = parse_any(fixture.read_bytes(), combined)
    assert source.id == "maryland-emma-email"
    assert notice.title == "Educational Materials"


def test_unrecognised_mail_reports_why_each_source_declined(riverbend):
    message = EmailMessage()
    message["Subject"] = "Your invoice is ready"
    message["From"] = "billing@elsewhere.example"
    message.set_content("nothing to do with procurement")

    with pytest.raises(NoticeEmailError, match="riverbend"):
        parse_any(message.as_bytes(), (riverbend,))


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"fields": {"nickname": "Nickname"}}, "unknown field"),
        ({"senders": (), "sender_domains": ()}, "needs senders"),
        ({"subject_pattern": "([unclosed"}, "invalid subject_pattern"),
        ({"subject_pattern": "no capture group", "fields": {}}, "needs a 'title'"),
    ],
)
def test_a_broken_source_entry_is_rejected_when_the_config_loads(bad, message):
    settings = {
        "id": "broken",
        "name": "Broken",
        "subject_pattern": "(?P<title>.+)",
        "fields": {"title": "Title"},
        "senders": ("a@b.example",),
    }
    settings.update(bad)

    with pytest.raises(ValueError, match=message):
        EmailSourceConfig(**settings)
