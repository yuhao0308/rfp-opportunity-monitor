import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rfp_monitor.models import Opportunity, SourceConfig
from rfp_monitor.state import StateStore

SOURCE = SourceConfig(
    id="test",
    name="Test Portal",
    state="TS",
    url="https://example.test/rfps",
)


def opportunity(title: str, status: str = "Open") -> Opportunity:
    return Opportunity(
        source_id=SOURCE.id,
        source_name=SOURCE.name,
        state=SOURCE.state,
        browse_url=SOURCE.url,
        external_id=title.lower().replace(" ", "-"),
        title=title,
        status=status,
    )


class StateStoreTests(unittest.TestCase):
    def test_first_scan_is_silent_and_second_scan_deduplicates(self):
        with TemporaryDirectory() as temp, StateStore(
            Path(temp) / "state.sqlite3"
        ) as state:
            first = state.process_successful_scan(SOURCE, [opportunity("One")])
            second = state.process_successful_scan(SOURCE, [opportunity("One")])
            self.assertEqual(first, [])
            self.assertEqual(second, [])
            self.assertEqual(state.record_count(SOURCE.id), 1)

    def test_new_and_materially_changed_records_are_returned(self):
        with TemporaryDirectory() as temp, StateStore(
            Path(temp) / "state.sqlite3"
        ) as state:
            state.process_successful_scan(SOURCE, [opportunity("One")])
            changes = state.process_successful_scan(
                SOURCE,
                [opportunity("One", "Responses Received"), opportunity("Two")],
            )
            self.assertEqual(
                [(change.kind, change.opportunity.title) for change in changes],
                [("changed", "One"), ("new", "Two")],
            )

    def test_baseline_can_be_explicitly_included(self):
        with TemporaryDirectory() as temp, StateStore(
            Path(temp) / "state.sqlite3"
        ) as state:
            changes = state.process_successful_scan(
                SOURCE, [opportunity("One")], include_baseline=True
            )
            self.assertEqual([change.kind for change in changes], ["new"])


if __name__ == "__main__":
    unittest.main()
