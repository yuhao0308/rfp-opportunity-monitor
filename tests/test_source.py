from __future__ import annotations

import json
from pathlib import Path

import pytest

from rfp_monitor.models import SourceConfig
from rfp_monitor.source import (
    AccessChallenge,
    JaggaerScanner,
    SourceError,
    normalize_table_rows,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


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


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ({"max_pages": 0}, "max_pages"),
        ({"navigation_timeout_seconds": 0}, "navigation_timeout_seconds"),
    ],
)
def test_scanner_rejects_invalid_limits(
    argument: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        JaggaerScanner(**argument)
