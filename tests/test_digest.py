from datetime import datetime, timedelta, timezone

from rfp_monitor.digest import _format_timestamp


def test_digest_timestamp_is_portable_across_operating_systems() -> None:
    eastern = timezone(timedelta(hours=-4), "EDT")
    timestamp = datetime(2026, 8, 3, 8, 5, tzinfo=eastern)

    assert _format_timestamp(timestamp) == "August 3, 2026 at 8:05 AM EDT"
