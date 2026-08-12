from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from rfp_monitor.emma_email import (
    SOURCE_ID,
    EmmaEmailError,
    EmmaNotice,
    parse_emma_email,
)
from rfp_monitor.mailer import build_forward
from rfp_monitor.matching import KeywordMatcher
from rfp_monitor.state import StateStore

FIXTURES = Path(__file__).parent / "fixtures" / "emails"


def load(name: str) -> EmmaNotice:
    return parse_emma_email((FIXTURES / name).read_bytes())


@pytest.fixture
def matcher() -> KeywordMatcher:
    from rfp_monitor.matching import load_keyword_library

    root = Path(__file__).resolve().parents[1]
    return KeywordMatcher(load_keyword_library(root / "config" / "keywords.json"))


def test_parses_every_labelled_field():
    notice = load("01-educational-materials.eml")

    assert notice.rfx_name == "Educational Materials"
    assert notice.bpm_id == "58348"
    assert notice.commodity == "Other"
    assert notice.lot == "UNDEFINED"
    assert notice.round_number == "1"
    assert notice.end_date == "8/14/2026"
    assert notice.requester == "Augustus Woyah"
    assert notice.message_id == "<emma-58348-r1@maryland.gov>"


def test_long_rfx_name_is_not_truncated_by_plain_text_wrapping():
    notice = load("03-days-cove-disc-golf.eml")

    assert notice.rfx_name == (
        "25228 GX0 Days Cove Park Disc Golf Course, 6425 Days Cove Road, "
        "White Marsh, Maryland 21162"
    )


def test_requester_name_never_reaches_the_matcher(matcher):
    """A requester called "Dean" must not inject a leadership signal."""

    notice = load("04-executive-search.eml")
    opportunity = notice.to_opportunity()

    assert notice.requester == "Dean Whitfield"
    assert opportunity.agency == ""
    assert "Whitfield" not in opportunity.searchable_text


def test_uninformative_commodity_is_dropped_but_a_real_one_is_kept():
    assert load("01-educational-materials.eml").to_opportunity().category == ""
    assert load("02-tmdl-impervious-surface.eml").to_opportunity().category == (
        "Heavy construction machinery and equipment"
    )


def test_solicitation_link_is_preferred_over_the_notification_settings_link():
    notice = load("02-tmdl-impervious-surface.eml")

    assert notice.link.startswith(
        "https://emma.maryland.gov/page.aspx/en/rfp/request_view/57567"
    )
    assert "notifications" not in notice.link


def test_non_solicitation_message_is_rejected():
    with pytest.raises(EmmaEmailError, match="not a solicitation notice"):
        load("05-maintenance-notice.eml")


def test_unexpected_sender_is_rejected():
    raw = (FIXTURES / "01-educational-materials.eml").read_bytes()
    spoofed = raw.replace(b"no-reply.emma@maryland.gov", b"phish@example.invalid")

    with pytest.raises(EmmaEmailError, match="unexpected sender"):
        parse_emma_email(spoofed)


def test_plain_text_only_message_still_parses():
    message = BytesParser(policy=policy.default).parsebytes(
        (FIXTURES / "01-educational-materials.eml").read_bytes()
    )
    plain = message.get_body(preferencelist=("plain",)).get_content()
    rebuilt = (
        b"From: no-reply.emma@maryland.gov\r\n"
        b"Subject: New / Updated Solicitation:  Educational Materials\r\n"
        b"Message-ID: <plain-only@maryland.gov>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n" + plain.encode()
    )

    notice = parse_emma_email(rebuilt)

    assert notice.bpm_id == "58348"
    assert notice.end_date == "8/14/2026"
    assert notice.link == ""


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("01-educational-materials.eml", None),
        ("02-tmdl-impervious-surface.eml", None),
        ("03-days-cove-disc-golf.eml", None),
        ("04-executive-search.eml", "Strong opportunity"),
    ],
)
def test_real_samples_classify_against_the_full_taxonomy(matcher, fixture, expected):
    result = matcher.match(load(fixture).to_opportunity())

    assert result.classification == expected


def test_record_key_is_stable_across_rounds_but_fingerprint_changes():
    round_one = load("01-educational-materials.eml").to_opportunity()
    round_two = load("01-educational-materials.eml").to_opportunity()
    round_two = type(round_two)(**{**round_two.to_dict(), "status": "Round 2"})

    assert round_one.record_key == round_two.record_key
    assert round_one.fingerprint != round_two.fingerprint


def test_a_rotating_link_token_does_not_look_like_a_change():
    notice = load("01-educational-materials.eml")
    resent = EmmaNotice(**{**notice.to_dict(), "link": notice.link + "&sid=999"})

    assert notice.to_opportunity().fingerprint == resent.to_opportunity().fingerprint


def test_preview_does_not_consume_the_change(tmp_path):
    opportunity = load("04-executive-search.eml").to_opportunity()

    with StateStore(tmp_path / "state.sqlite3") as state:
        preview = state.record_opportunity(SOURCE_ID, opportunity, persist=False)
        still_new = state.record_opportunity(SOURCE_ID, opportunity, persist=True)
        after = state.record_opportunity(SOURCE_ID, opportunity, persist=True)

    assert preview is not None and preview.kind == "new"
    assert still_new is not None and still_new.kind == "new"
    assert after is None


def test_round_update_reports_a_change(tmp_path):
    first = load("01-educational-materials.eml").to_opportunity()
    updated = type(first)(**{**first.to_dict(), "status": "Round 2"})

    with StateStore(tmp_path / "state.sqlite3") as state:
        state.record_opportunity(SOURCE_ID, first)
        change = state.record_opportunity(SOURCE_ID, updated)

    assert change is not None and change.kind == "changed"


def test_processed_email_ledger_round_trips(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        assert state.email_is_processed(SOURCE_ID, "<a@b>") is False
        state.mark_email_processed(SOURCE_ID, "<a@b>", subject="s", outcome="forward")

        assert state.email_is_processed(SOURCE_ID, "<a@b>") is True
        assert state.email_is_processed(SOURCE_ID, "") is False


def test_forward_carries_the_reason_and_the_original_attachment(matcher):
    raw = (FIXTURES / "04-executive-search.eml").read_bytes()
    original = BytesParser(policy=policy.default).parsebytes(raw)
    notice = parse_emma_email(original)
    match = matcher.match(notice.to_opportunity())

    forward = build_forward(
        ["ks973111@gmail.com"], notice, match, "new", original, sender="bot@example.test"
    )
    attachments = list(forward.iter_attachments())
    body = forward.get_body(preferencelist=("plain",)).get_content()

    assert forward["Subject"] == (
        "[RFP Monitor] Strong opportunity: "
        "Executive Search Services for the Next President of the University"
    )
    assert forward["To"] == "ks973111@gmail.com"
    assert 'Priority 1: "executive search"' in body
    assert "BPM ID: 59001" in body
    assert notice.link in body
    assert [item.get_content_type() for item in attachments] == ["message/rfc822"]
