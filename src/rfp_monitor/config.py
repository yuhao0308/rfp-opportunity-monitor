from __future__ import annotations

import tomllib
from dataclasses import dataclass
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


@dataclass(frozen=True)
class NotificationConfig:
    recipients: tuple[str, ...]
    subject_prefix: str = "[RFP Monitor]"
    timezone: str = "America/New_York"


@dataclass(frozen=True)
class AppConfig:
    monitor: MonitorConfig
    notifications: NotificationConfig
    sources: tuple[SourceConfig, ...]


def _resolve(base: Path, raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    data = tomllib.loads(config_path.read_text())
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
    )
    notifications = NotificationConfig(
        recipients=tuple(notifications_data.get("recipients", [])),
        subject_prefix=str(notifications_data.get("subject_prefix", "[RFP Monitor]")),
        timezone=str(notifications_data.get("timezone", "America/New_York")),
    )
    sources = tuple(SourceConfig(**entry) for entry in data.get("sources", []))
    enabled_ids = [source.id for source in sources if source.enabled]
    if not enabled_ids:
        raise ValueError("At least one source must be enabled")
    if len(enabled_ids) != len(set(enabled_ids)):
        raise ValueError("Source IDs must be unique")
    return AppConfig(monitor=monitor, notifications=notifications, sources=sources)

