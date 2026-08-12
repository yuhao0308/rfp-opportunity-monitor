import pytest

from rfp_monitor.config import ScheduleConfig, load_config

BASE = """
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
"""


def write(tmp_path, extra: str = ""):
    path = tmp_path / "config.toml"
    path.write_text(BASE + extra, encoding="utf-8")
    return path


def test_schedule_defaults_to_seven_in_the_morning(tmp_path):
    schedule = load_config(write(tmp_path)).schedule

    assert (schedule.hour, schedule.minute) == (7, 0)
    assert schedule.clock == "07:00"
    assert schedule.forward is True


def test_schedule_is_read_from_config(tmp_path):
    schedule = load_config(
        write(tmp_path, "\n[schedule]\nhour = 6\nminute = 30\nforward = false\n")
    ).schedule

    assert schedule.clock == "06:30"
    assert schedule.forward is False


@pytest.mark.parametrize(("hour", "minute"), [(24, 0), (-1, 0), (7, 60), (7, -1)])
def test_out_of_range_schedule_is_rejected(hour, minute):
    with pytest.raises(ValueError, match="schedule"):
        ScheduleConfig(hour=hour, minute=minute)


def test_forward_recipients_fall_back_to_the_digest_recipients(tmp_path):
    email = load_config(write(tmp_path)).email

    assert email.forward_to == ("digest@example.test",)


def test_blank_forward_recipients_are_dropped(tmp_path):
    email = load_config(
        write(tmp_path, '\n[email]\nforward_to = ["", "  ", "real@example.test"]\n')
    ).email

    assert email.forward_to == ("real@example.test",)
