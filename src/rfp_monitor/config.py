from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import SourceConfig


@dataclass(frozen=True)
class MonitorConfig:
    keyword_path: Path
    state_path: Path
    headless: bool = True
    max_pages: int = 50
    navigation_timeout_seconds: int = 45
    profile_path: Path | None = None
    retry_attempts: int = 3
    retry_backoff_seconds: float = 2.0
    request_delay_seconds: float = 1.0


@dataclass(frozen=True)
class NotificationConfig:
    recipients: tuple[str, ...]
    subject_prefix: str = "[RFP Monitor]"
    timezone: str = "America/New_York"


@dataclass(frozen=True)
class EmailConfig:
    """Inbox ingestion for portals that notify by email instead of a public list."""

    enabled: bool = False
    folder: str = "INBOX"
    senders: tuple[str, ...] = ("no-reply.emma@maryland.gov",)
    forward_to: tuple[str, ...] = ()
    since_days: int = 7
    max_messages: int = 200


NOTICE_FIELDS = (
    "title",
    "external_id",
    "category",
    "lot",
    "round_number",
    "due_date",
    "requester",
)


@dataclass(frozen=True)
class EmailSourceConfig:
    """How to recognise and read one portal's notification email.

    Everything portal-specific lives here, so a new source is a config entry
    rather than a new parser: who it comes from, how its subject reads, and
    which label sits in front of each field in the body.
    """

    id: str
    name: str
    subject_pattern: str
    fields: dict[str, str] = field(default_factory=dict)
    state: str = ""
    senders: tuple[str, ...] = ()
    sender_domains: tuple[str, ...] = ()
    browse_url: str = ""
    link_text: str = "Link"
    link_hosts: tuple[str, ...] = ()
    uninformative: tuple[str, ...] = ("undefined", "n/a", "none", "other")
    enabled: bool = True

    def __post_init__(self) -> None:
        unknown = sorted(set(self.fields) - set(NOTICE_FIELDS))
        if unknown:
            raise ValueError(
                f"email source {self.id!r} maps unknown field(s) {', '.join(unknown)}; "
                f"allowed: {', '.join(NOTICE_FIELDS)}"
            )
        if not self.senders and not self.sender_domains:
            raise ValueError(f"email source {self.id!r} needs senders or sender_domains")
        try:
            compiled = re.compile(self.subject_pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"email source {self.id!r} has an invalid subject_pattern: {exc}")
        if "title" not in self.fields and "title" not in compiled.groupindex:
            raise ValueError(
                f"email source {self.id!r} needs a 'title' field mapping or a "
                "(?P<title>...) group in subject_pattern"
            )


@dataclass(frozen=True)
class ScheduleConfig:
    """When the unattended email-scan runs, in the machine's local time."""

    hour: int = 7
    minute: int = 0
    forward: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ValueError(f"schedule.hour must be 0-23, got {self.hour}")
        if not 0 <= self.minute <= 59:
            raise ValueError(f"schedule.minute must be 0-59, got {self.minute}")

    @property
    def clock(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class AppConfig:
    monitor: MonitorConfig
    notifications: NotificationConfig
    sources: tuple[SourceConfig, ...]
    email: EmailConfig = EmailConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    email_sources: tuple[EmailSourceConfig, ...] = ()

    @property
    def enabled_email_sources(self) -> tuple[EmailSourceConfig, ...]:
        return tuple(source for source in self.email_sources if source.enabled)


def _resolve(base: Path, raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    monitor_data = data.get("monitor", {})
    notifications_data = data.get("notifications", {})

    keyword_path = _resolve(base, monitor_data.get("keyword_path", "config/keywords.json"))
    state_path = _resolve(base, monitor_data.get("state_path", "var/rfp-monitor.sqlite3"))
    assert keyword_path and state_path

    monitor = MonitorConfig(
        keyword_path=keyword_path,
        state_path=state_path,
        headless=bool(monitor_data.get("headless", True)),
        max_pages=int(monitor_data.get("max_pages", 50)),
        navigation_timeout_seconds=int(
            monitor_data.get("navigation_timeout_seconds", 45)
        ),
        profile_path=_resolve(base, monitor_data.get("profile_path")),
        retry_attempts=int(monitor_data.get("retry_attempts", 3)),
        retry_backoff_seconds=float(monitor_data.get("retry_backoff_seconds", 2.0)),
        request_delay_seconds=float(monitor_data.get("request_delay_seconds", 1.0)),
    )
    notifications = NotificationConfig(
        recipients=tuple(notifications_data.get("recipients", [])),
        subject_prefix=str(notifications_data.get("subject_prefix", "[RFP Monitor]")),
        timezone=str(notifications_data.get("timezone", "America/New_York")),
    )
    email_data = data.get("email", {})
    email = EmailConfig(
        enabled=bool(email_data.get("enabled", False)),
        folder=str(email_data.get("folder", "INBOX")),
        senders=tuple(email_data.get("senders", ("no-reply.emma@maryland.gov",))),
        # Fall back to the digest recipients so one address is configured in one place.
        forward_to=tuple(
            address
            for address in email_data.get(
                "forward_to", notifications_data.get("recipients", [])
            )
            if str(address).strip()
        ),
        since_days=int(email_data.get("since_days", 7)),
        max_messages=int(email_data.get("max_messages", 200)),
    )

    email_sources = tuple(
        EmailSourceConfig(
            id=str(entry["id"]),
            name=str(entry.get("name", entry["id"])),
            subject_pattern=str(entry["subject_pattern"]),
            fields={str(key): str(value) for key, value in entry.get("fields", {}).items()},
            state=str(entry.get("state", "")),
            senders=tuple(entry.get("senders", ())),
            sender_domains=tuple(entry.get("sender_domains", ())),
            browse_url=str(entry.get("browse_url", "")),
            link_text=str(entry.get("link_text", "Link")),
            link_hosts=tuple(entry.get("link_hosts", ())),
            uninformative=tuple(
                entry.get("uninformative", ("undefined", "n/a", "none", "other"))
            ),
            enabled=bool(entry.get("enabled", True)),
        )
        for entry in data.get("email_sources", [])
    )
    ids = [source.id for source in email_sources]
    if len(ids) != len(set(ids)):
        raise ValueError("Email source IDs must be unique")

    schedule_data = data.get("schedule", {})
    schedule = ScheduleConfig(
        hour=int(schedule_data.get("hour", 7)),
        minute=int(schedule_data.get("minute", 0)),
        forward=bool(schedule_data.get("forward", True)),
    )

    sources = tuple(SourceConfig(**entry) for entry in data.get("sources", []))
    enabled_ids = [source.id for source in sources if source.enabled]
    if not enabled_ids:
        raise ValueError("At least one source must be enabled")
    if len(enabled_ids) != len(set(enabled_ids)):
        raise ValueError("Source IDs must be unique")
    return AppConfig(
        monitor=monitor,
        notifications=notifications,
        sources=sources,
        email=email,
        schedule=schedule,
        email_sources=email_sources,
    )
