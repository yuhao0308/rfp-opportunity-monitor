from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from .models import MatchResult, Opportunity

GROUPS = (
    "priority_1",
    "priority_2",
    "context",
    "leadership",
    "procurement",
    "market_intelligence",
    "exploratory",
    "noise",
    "naics",
)

_WEIGHTS = {
    "priority_1": 100,
    "priority_2": 45,
    "context": 15,
    "leadership": 15,
    "procurement": 10,
    "market_intelligence": 60,
    "exploratory": 30,
    "noise": -20,
    "naics": 35,
}

_SERVICE_SIGNAL = re.compile(
    r"(?<!\w)(?:search|recruitment|recruiting|placement|coaching|consulting|consultant)(?!\w)",
    re.IGNORECASE,
)
_CLOSED = re.compile(r"(?<!\w)closed(?!\w)", re.IGNORECASE)
_MARKET_STAGE = re.compile(
    r"(?<!\w)(?:award|awarded|responses?\s+received|bid\s+tabulation|cooperative)(?!\w)",
    re.IGNORECASE,
)
_MARKET_NOTICE = re.compile(
    r"(?<!\w)(?:intent\s+to\s+award|notice\s+of\s+award|contract\s+award|"
    r"award\s+notice|cooperative\s+purchasing\s+agreement|"
    r"intent\s+to\s+(?:enter|participate))(?!\w)",
    re.IGNORECASE,
)


def load_keyword_groups(path: str | Path) -> dict[str, tuple[str, ...]]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise TypeError("Keyword library must be a JSON object")

    groups: dict[str, tuple[str, ...]] = {}
    for name in GROUPS:
        values = data.get(name, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"Keyword group {name!r} must be a list of strings")
        groups[name] = tuple(value.strip() for value in values if value.strip())
    return groups


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _parse_date(value: str) -> date | None:
    raw = value.strip()
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        pass

    candidates = [raw]
    for pattern in (
        r"\d{4}-\d{2}-\d{2}",
        r"\d{1,2}/\d{1,2}/\d{2,4}",
        r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}",
    ):
        match = re.search(pattern, raw)
        if match:
            candidates.append(match.group())

    for candidate in candidates:
        for format_string in (
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%B %d, %Y",
            "%B %d %Y",
            "%b %d, %Y",
            "%b %d %Y",
        ):
            try:
                return datetime.strptime(candidate, format_string).replace(tzinfo=UTC).date()
            except ValueError:
                continue
    return None


class KeywordMatcher:
    def __init__(
        self,
        groups: Mapping[str, Sequence[str]],
        *,
        today: date | None = None,
    ):
        self.groups = {
            name: tuple(
                dict.fromkeys(term.strip() for term in groups.get(name, ()) if term.strip())
            )
            for name in GROUPS
        }
        self._patterns = {
            name: tuple((term, _phrase_pattern(term)) for term in terms)
            for name, terms in self.groups.items()
        }
        self.today = today

    @classmethod
    def from_file(cls, path: str | Path, *, today: date | None = None) -> KeywordMatcher:
        return cls(load_keyword_groups(path), today=today)

    def _matches(self, text: str) -> dict[str, tuple[str, ...]]:
        matched = {}
        for name, patterns in self._patterns.items():
            terms = tuple(term for term, pattern in patterns if pattern.search(text))
            if terms:
                matched[name] = terms
        return matched

    def _score(self, matched: Mapping[str, tuple[str, ...]]) -> int:
        return sum(_WEIGHTS[name] for name in matched)

    def match(self, opportunity: Opportunity) -> MatchResult:
        text = opportunity.searchable_text
        matched = self._matches(text)
        score = self._score(matched)

        if _CLOSED.search(opportunity.status):
            return MatchResult(
                classification=None,
                score=score,
                matched=matched,
                reason="Suppressed: status is Closed.",
                suppressed=True,
            )

        if matched.get("market_intelligence"):
            term = matched["market_intelligence"][0]
            return MatchResult(
                classification="Market intelligence",
                score=score,
                matched=matched,
                reason=f'Market watchlist: "{term}".',
            )

        classification: str | None = None
        reason = "No qualifying keyword combination."
        service_signal = bool(_SERVICE_SIGNAL.search(text))
        leadership_qualified = bool(matched.get("leadership")) and service_signal

        if matched.get("priority_1"):
            classification = "Strong opportunity"
            reason = f'Priority 1: "{matched["priority_1"][0]}".'
        elif matched.get("priority_2"):
            corroborators = [
                name
                for name, present in (
                    ("context", bool(matched.get("context"))),
                    ("leadership", bool(matched.get("leadership"))),
                    ("procurement", bool(matched.get("procurement"))),
                    ("NAICS", bool(matched.get("naics"))),
                )
                if present
            ]
            if corroborators:
                classification = "Possible opportunity"
                joined = ", ".join(corroborators)
                reason = f'Priority 2: "{matched["priority_2"][0]}"; corroborated by {joined}.'
        elif leadership_qualified:
            classification = "Possible opportunity"
            reason = f'Leadership: "{matched["leadership"][0]}"; paired with a service signal.'
        elif matched.get("exploratory") and matched.get("context"):
            classification = "Possible opportunity"
            reason = f'Exploratory: "{matched["exploratory"][0]}"; corroborated by context.'

        if classification:
            current_day = self.today or datetime.now(UTC).date()
            due = _parse_date(opportunity.due_date)
            past_due = due is not None and due < current_day
            market_stage = bool(
                _MARKET_STAGE.search(opportunity.status) or _MARKET_NOTICE.search(opportunity.title)
            )
            if past_due or market_stage:
                causes = []
                if past_due:
                    causes.append("past due")
                if market_stage:
                    causes.append("in an award/response/cooperative stage")
                reason = (
                    f"{reason} Routed to market intelligence because it is {' and '.join(causes)}."
                )
                classification = "Market intelligence"

        return MatchResult(
            classification=classification,
            score=score,
            matched=matched,
            reason=reason,
        )

    __call__ = match


def match_opportunity(
    opportunity: Opportunity,
    groups: Mapping[str, Sequence[str]],
    *,
    today: date | None = None,
) -> MatchResult:
    return KeywordMatcher(groups, today=today).match(opportunity)


Matcher = KeywordMatcher
load_keyword_library = load_keyword_groups
