from __future__ import annotations

import json
from pathlib import Path

import pytest

from rfp_monitor.models import SourceConfig
from rfp_monitor.source import (
    AccessChallenge,
    JaggaerScanner,
    SourceError,
    next_alabama_page,
    normalize_alabama_date,
    normalize_alabama_rows,
    normalize_table_rows,
    parse_alabama_detail,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_normalizes_live_north_dakota_headers_and_deduplicates() -> None:
    source = SourceConfig(
        id="north-dakota",
        name="North Dakota Buys",
        state="ND",
        url="https://public.ndbuys.nd.gov/page.aspx/en/rfp/request_browse_public",
    )
    table = _fixture("north_dakota_table.json")

    opportunities = normalize_table_rows(source, table["headers"], table["rows"])

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.title == "Executive Search Services"
    assert opportunity.published_date == "7/20/2026 8:00 AM"
    assert opportunity.due_date == "8/05/2026 2:00 PM"
    assert opportunity.category == "Executive Search Services"
    assert opportunity.status == "Open"
    assert opportunity.external_id == ""
    assert opportunity.detail_url == (
        "https://public.ndbuys.nd.gov/page.aspx/en/bpm/process_manage_extranet/3596"
    )


def test_normalizes_maryland_headers_and_detail_links() -> None:
    source = SourceConfig(
        id="maryland",
        name="Maryland eMMA",
        state="MD",
        url="https://emma.maryland.gov/page.aspx/en/rfp/request_browse_public?historyBack=1",
    )
    table = _fixture("maryland_table.json")

    opportunities = normalize_table_rows(source, table["headers"], table["rows"])

    assert [item.external_id for item in opportunities] == ["BPM048201", "BPM048202"]
    assert opportunities[0].title == "Higher Education Executive Recruitment"
    assert opportunities[0].due_date == "08/15/2026 2:00 PM"
    assert opportunities[0].category == "Management advisory services"
    assert opportunities[0].solicitation_type == "Request for Proposals"
    assert opportunities[0].agency == "University System of Maryland"
    assert opportunities[0].detail_url == (
        "https://emma.maryland.gov/page.aspx/en/bpm/process_manage_extranet/48201"
    )
    assert opportunities[1].status == "Responses Received"


def test_requires_a_recognized_title_column() -> None:
    source = SourceConfig("source", "Source", "ST", "https://example.test/rfps")

    with pytest.raises(SourceError, match="no recognized title column"):
        normalize_table_rows(source, ["ID", "Status"], [["1", "Open"]])


def test_skips_blank_and_titleless_rows() -> None:
    source = SourceConfig("source", "Source", "ST", "https://example.test/rfps")

    assert (
        normalize_table_rows(
            source,
            ["ID", "Title", "Status"],
            [["", "", ""], ["1", "", "Open"]],
        )
        == []
    )


def test_scanner_validates_usage_without_importing_browser() -> None:
    source = SourceConfig("source", "Source", "ST", "https://example.test/rfps")
    scanner = JaggaerScanner(max_pages=1)

    with pytest.raises(SourceError, match="context manager"):
        scanner.scan(source)
    assert issubclass(AccessChallenge, SourceError)


def test_normalizes_alabama_rows_and_deduplicates() -> None:
    source = SourceConfig(
        id="alabama",
        name="Alabama Public RFP Search",
        state="AL",
        url="https://rfp.alabama.gov/PublicView.aspx",
        adapter="alabama-rfp",
    )
    rows = _fixture("alabama_rows.json")

    opportunities = normalize_alabama_rows(source, rows)

    assert len(opportunities) == 2
    assert opportunities[0].external_id == "2026-165-04"
    assert opportunities[0].title == "Graphic design services"
    assert opportunities[0].category == (
        "COMMUNICATIONS & MEDIA RELATED SERVICES | GRAPHIC ARTS SERVICES (NOT PRINTING)"
    )
    assert opportunities[1].external_id == "RFP-011 -26000000009"
    assert opportunities[1].detail_url.endswith(
        "searchSolicitation.jsp?query=RFP%40011%4026000000009%401"
    )


def test_parses_alabama_detail_and_normalizes_dates() -> None:
    payload = """
    Laboratory Facility Operations and Management Provider (RFP: 26000000009)*
    noab*noab*08/07/26 5:00pm CDT*Public Health***Prof Services*
    Request for Proposals (RFP)*Laboratory Facility Operations and Management Provider*5*
    """

    detail = parse_alabama_detail(payload)

    assert detail == {
        "title": "Laboratory Facility Operations and Management Provider",
        "due_date": "2026-08-07T17:00:00-05:00",
        "agency": "Public Health",
        "category": "Prof Services",
        "solicitation_type": "Request for Proposals (RFP)",
    }
    assert normalize_alabama_date("8/17/2026") == "2026-08-17"
    assert normalize_alabama_date("noab") == ""
    assert normalize_alabama_date("unknown") == "unknown"


def test_malformed_alabama_detail_is_ignored() -> None:
    assert parse_alabama_detail("not a STAARS detail response") == {}


def test_alabama_pagination_requires_the_next_sequential_page() -> None:
    assert next_alabama_page("1", ["2", "3", "11"]) == 2
    assert next_alabama_page(10, ["1", "...", "11"]) == 11
    assert next_alabama_page(2, ["1", "4"]) is None
    assert next_alabama_page("unknown", ["2"]) is None


def test_scanner_retries_timeout_and_fails_safely() -> None:
    class TimeoutPage:
        def goto(self, *_: object, **__: object) -> None:
            raise TimeoutError("navigation timed out")

        def close(self) -> None:
            return None

    class TimeoutContext:
        def __init__(self) -> None:
            self.attempts = 0

        def new_page(self) -> TimeoutPage:
            self.attempts += 1
            return TimeoutPage()

    context = TimeoutContext()
    scanner = JaggaerScanner(retry_attempts=2, retry_backoff_seconds=0)
    scanner._context = context
    source = SourceConfig("source", "Source", "ST", "https://example.test/rfps")

    with pytest.raises(SourceError, match="navigation timed out"):
        scanner.scan(source)
    assert context.attempts == 2


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ({"max_pages": 0}, "max_pages"),
        ({"navigation_timeout_seconds": 0}, "navigation_timeout_seconds"),
        ({"retry_attempts": 0}, "retry_attempts"),
        ({"retry_backoff_seconds": -1}, "retry_backoff_seconds"),
        ({"request_delay_seconds": -1}, "request_delay_seconds"),
    ],
)
def test_scanner_rejects_invalid_limits(
    argument: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        JaggaerScanner(**argument)
