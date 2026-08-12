from datetime import date

import pytest

from rfp_monitor.matching import KeywordMatcher
from rfp_monitor.models import Opportunity


def opportunity(title: str, **changes: str) -> Opportunity:
    values = {
        "source_id": "test",
        "source_name": "Test Portal",
        "state": "XX",
        "browse_url": "https://example.test/rfps",
        "title": title,
    }
    values.update(changes)
    return Opportunity(**values)


@pytest.fixture
def groups() -> dict[str, tuple[str, ...]]:
    return {
        "priority_1": ("executive search",),
        "priority_2": ("candidate sourcing", "organizational development"),
        "context": ("higher education", "university"),
        "leadership": ("president",),
        "procurement": ("RFP",),
        "market_intelligence": ("Isaacson Miller",),
        "exploratory": ("campus event planning",),
        "noise": ("software license",),
        "naics": ("611710 — Educational Support Services",),
    }


def test_priority_one_is_case_insensitive_and_whole_phrase(groups):
    matcher = KeywordMatcher(groups)

    hit = matcher.match(opportunity("EXECUTIVE SEARCH services"))
    miss = matcher.match(opportunity("Nonexecutive searchlight services"))

    assert hit.classification == "Strong opportunity"
    assert hit.matched["priority_1"] == ("executive search",)
    assert miss.classification is None


def test_noise_reduces_score_but_does_not_override_direct_match(groups):
    matcher = KeywordMatcher(groups)

    clean = matcher.match(opportunity("Executive search"))
    noisy = matcher.match(opportunity("Executive search and software license"))

    assert noisy.classification == "Strong opportunity"
    assert noisy.score < clean.score
    assert noisy.matched["noise"] == ("software license",)


@pytest.mark.parametrize(
    ("changes", "expected_group"),
    [
        ({"category": "Higher education"}, "context"),
        ({"solicitation_type": "RFP"}, "procurement"),
        ({"agency": "Office of the President for recruitment"}, "leadership"),
    ],
)
def test_priority_two_requires_allowed_corroboration(groups, changes, expected_group):
    matcher = KeywordMatcher(groups)

    result = matcher.match(opportunity("Candidate sourcing", **changes))

    assert result.classification == "Possible opportunity"
    assert expected_group in result.matched
    assert expected_group in result.reason


def test_priority_two_without_corroboration_is_not_relevant(groups):
    result = KeywordMatcher(groups).match(opportunity("Candidate sourcing"))

    assert result.classification is None
    assert result.matched == {"priority_2": ("candidate sourcing",)}


def test_priority_two_is_corroborated_by_a_leadership_title(groups):
    result = KeywordMatcher(groups).match(
        opportunity("Candidate sourcing for the next president")
    )

    assert result.classification == "Possible opportunity"
    assert set(result.matched) >= {"priority_2", "leadership"}


def test_leadership_requires_service_signal(groups):
    matcher = KeywordMatcher(groups)

    leader_only = matcher.match(opportunity("University president"))
    consulting = matcher.match(opportunity("Consulting services for the president"))

    assert leader_only.classification is None
    assert consulting.classification == "Possible opportunity"
    assert consulting.matched["leadership"] == ("president",)


def test_exploratory_requires_context(groups):
    matcher = KeywordMatcher(groups)

    alone = matcher.match(opportunity("Campus event planning"))
    contextual = matcher.match(opportunity("Campus event planning for a university"))

    assert alone.classification is None
    assert contextual.classification == "Possible opportunity"
    assert set(contextual.matched) >= {"exploratory", "context"}


def test_market_watchlist_routes_to_market_intelligence(groups):
    result = KeywordMatcher(groups).match(opportunity("Notice involving ISAACSON MILLER"))

    assert result.classification == "Market intelligence"
    assert result.matched["market_intelligence"] == ("Isaacson Miller",)
    assert "Market watchlist" in result.reason


@pytest.mark.parametrize(
    "changes",
    [
        {"due_date": "2026-07-29"},
        {"status": "Responses Received"},
        {"status": "Awarded"},
        {"title": "Executive search cooperative purchasing agreement"},
    ],
)
def test_relevant_non_actionable_records_route_to_market_intelligence(groups, changes):
    matcher = KeywordMatcher(groups, today=date(2026, 7, 30))
    title = changes.get("title", "Executive search")
    item = opportunity(title, **{key: value for key, value in changes.items() if key != "title"})

    result = matcher.match(item)

    assert result.classification == "Market intelligence"
    assert "Routed to market intelligence" in result.reason


def test_future_due_date_remains_actionable(groups):
    result = KeywordMatcher(groups, today=date(2026, 7, 30)).match(
        opportunity("Executive search", due_date="07/31/2026 2:00 PM")
    )

    assert result.classification == "Strong opportunity"


def test_explicit_closed_status_suppresses_even_a_direct_match(groups):
    result = KeywordMatcher(groups).match(
        opportunity("Executive search", status="Solicitation Closed")
    )

    assert result.classification is None
    assert result.suppressed is True
    assert result.matched["priority_1"] == ("executive search",)


def test_naics_phrase_alone_is_not_a_possible_opportunity(groups):
    result = KeywordMatcher(groups).match(opportunity("611710 — educational support services"))

    assert result.classification is None
    assert result.matched["naics"] == ("611710 — Educational Support Services",)


def test_naics_matches_the_descriptive_name_without_its_code(groups):
    """Portals and eMMA email print the category name without the NAICS number."""

    result = KeywordMatcher(groups).match(
        opportunity("Candidate sourcing", category="Educational support services")
    )

    assert result.matched["naics"] == ("611710 — Educational Support Services",)
    assert result.classification == "Possible opportunity"


def test_naics_can_corroborate_priority_two(groups):
    result = KeywordMatcher(groups).match(
        opportunity("Candidate sourcing: 611710 — Educational Support Services")
    )

    assert result.classification == "Possible opportunity"
    assert set(result.matched) >= {"priority_2", "naics"}
    assert "NAICS" in result.reason
