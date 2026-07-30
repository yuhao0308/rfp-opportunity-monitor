from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

from .models import Change, Opportunity, SourceConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_runs (
    source_id TEXT PRIMARY KEY,
    initialized_at TEXT NOT NULL,
    last_success_at TEXT NOT NULL,
    record_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    source_id TEXT NOT NULL,
    record_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    data_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (source_id, record_key)
);
"""


class StateStore(AbstractContextManager["StateStore"]):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.executescript(SCHEMA)

    def __exit__(self, *args: object) -> None:
        self.connection.close()

    def process_successful_scan(
        self,
        source: SourceConfig,
        opportunities: list[Opportunity],
        include_baseline: bool = False,
    ) -> list[Change]:
        now = datetime.now(UTC).isoformat()
        initialized = self.connection.execute(
            "SELECT 1 FROM source_runs WHERE source_id = ?", (source.id,)
        ).fetchone()
        changes: list[Change] = []

        with self.connection:
            for opportunity in opportunities:
                existing = self.connection.execute(
                    """
                    SELECT fingerprint FROM opportunities
                    WHERE source_id = ? AND record_key = ?
                    """,
                    (source.id, opportunity.record_key),
                ).fetchone()
                kind = None
                if existing is None:
                    kind = "new"
                elif existing[0] != opportunity.fingerprint:
                    kind = "changed"

                first_seen = now if existing is None else self.connection.execute(
                    """
                    SELECT first_seen_at FROM opportunities
                    WHERE source_id = ? AND record_key = ?
                    """,
                    (source.id, opportunity.record_key),
                ).fetchone()[0]
                self.connection.execute(
                    """
                    INSERT INTO opportunities (
                        source_id, record_key, fingerprint, data_json,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, record_key) DO UPDATE SET
                        fingerprint = excluded.fingerprint,
                        data_json = excluded.data_json,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        source.id,
                        opportunity.record_key,
                        opportunity.fingerprint,
                        json.dumps(opportunity.to_dict(), sort_keys=True),
                        first_seen,
                        now,
                    ),
                )
                if kind and (initialized or include_baseline):
                    changes.append(Change(kind=kind, opportunity=opportunity))

            self.connection.execute(
                """
                INSERT INTO source_runs (
                    source_id, initialized_at, last_success_at, record_count
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_success_at = excluded.last_success_at,
                    record_count = excluded.record_count
                """,
                (source.id, now, now, len(opportunities)),
            )
        return changes

    def source_is_initialized(self, source_id: str) -> bool:
        return bool(
            self.connection.execute(
                "SELECT 1 FROM source_runs WHERE source_id = ?", (source_id,)
            ).fetchone()
        )

    def record_count(self, source_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE source_id = ?", (source_id,)
        ).fetchone()
        return int(row[0])

