from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from hashlib import sha256


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    state: str
    url: str
    enabled: bool = True


@dataclass(frozen=True)
class Opportunity:
    source_id: str
    source_name: str
    state: str
    browse_url: str
    title: str
    external_id: str = ""
    detail_url: str = ""
    status: str = ""
    due_date: str = ""
    published_date: str = ""
    category: str = ""
    agency: str = ""
    solicitation_type: str = ""

    @property
    def record_key(self) -> str:
        stable = self.external_id or self.detail_url or self.title
        return sha256(f"{self.source_id}\0{stable}".encode()).hexdigest()

    @property
    def fingerprint(self) -> str:
        material = {
            key: value
            for key, value in asdict(self).items()
            if key not in {"browse_url", "source_name", "state"}
        }
        payload = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()

    @property
    def searchable_text(self) -> str:
        return " | ".join(
            value
            for value in (
                self.title,
                self.external_id,
                self.status,
                self.category,
                self.agency,
                self.solicitation_type,
            )
            if value
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> Opportunity:
        return cls(**data)


@dataclass(frozen=True)
class MatchResult:
    classification: str | None
    score: int
    matched: dict[str, tuple[str, ...]] = field(default_factory=dict)
    reason: str = ""
    suppressed: bool = False

    @property
    def is_relevant(self) -> bool:
        return self.classification is not None and not self.suppressed


@dataclass(frozen=True)
class Change:
    kind: str
    opportunity: Opportunity


@dataclass(frozen=True)
class Alert:
    change_kind: str
    opportunity: Opportunity
    match: MatchResult

