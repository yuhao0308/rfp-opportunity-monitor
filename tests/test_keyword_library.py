import json
from pathlib import Path

from rfp_monitor.matching import load_keyword_library

LIBRARY = Path(__file__).parents[1] / "config" / "keywords.json"


def test_full_framework_library_is_preserved():
    raw = json.loads(LIBRARY.read_text(encoding="utf-8"))
    groups = load_keyword_library(LIBRARY)

    assert raw["_meta"]["term_count"] == 1_141
    assert {name: len(terms) for name, terms in groups.items()} == raw["_meta"][
        "group_counts"
    ]
    assert sum(len(terms) for terms in groups.values()) == 1_141
    assert len({term.casefold() for terms in groups.values() for term in terms}) == 1_141
