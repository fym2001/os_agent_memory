from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import uuid4

from core.constants import EventType, MemoryType, Scene


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_enum(enum_cls: type[Enum], value: Any) -> Enum:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        for member in enum_cls:
            if normalized == member.value or normalized == member.name:
                return member
        lowered = normalized.lower()
        for member in enum_cls:
            if lowered == member.value.lower() or lowered == member.name.lower():
                return member
    raise ValueError(f"Cannot coerce {value!r} to {enum_cls.__name__}")


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        candidate = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(candidate)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Cannot parse datetime value: {value!r}") from exc
    raise ValueError(f"Cannot coerce {value!r} to datetime")


def _ensure_list(value: Sequence[Any] | None) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value)


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    return value


@dataclass(slots=True)
class MemoryEvent:
    event_id: str
    raw_event_id: str
    user_id: str
    session_id: str
    task_id: str
    event_type: EventType | str
    scenario: Scene | str
    source: str
    actor: str | None = None
    content: str | None = None
    tool_name: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    success: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | str = field(default_factory=_utc_now)
    raw_event: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.event_type = _coerce_enum(EventType, self.event_type)
        self.scenario = _coerce_enum(Scene, self.scenario)
        self.input = dict(self.input or {})
        self.output = dict(self.output or {})
        self.metadata = dict(self.metadata or {})
        self.timestamp = _coerce_datetime(self.timestamp)
        if self.raw_event is not None:
            self.raw_event = dict(self.raw_event)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(slots=True)
class MemoryCandidate:
    candidate_id: str = field(default_factory=lambda: uuid4().hex)
    user_id: str = ""
    memory_type: MemoryType | str = MemoryType.PREFERENCE
    key: str = ""
    content: str = ""
    scenario: Scene | str = Scene.GLOBAL
    confidence: float = 0.8
    source: str = "extracted"
    source_events: list[str] = field(default_factory=list)
    source_summaries: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        self.memory_type = _coerce_enum(MemoryType, self.memory_type)
        self.scenario = _coerce_enum(Scene, self.scenario)
        self.source_events = _ensure_list(self.source_events)
        self.source_summaries = _ensure_list(self.source_summaries)
        self.tags = _ensure_list(self.tags)
        self.metadata = dict(self.metadata or {})
        self.created_at = _coerce_datetime(self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))

    def with_context(self, *, user_id: str, scenario: Scene | str, source_event_id: str | None = None) -> "MemoryCandidate":
        self.user_id = user_id
        self.scenario = _coerce_enum(Scene, scenario)
        if source_event_id and source_event_id not in self.source_events:
            self.source_events.append(source_event_id)
        return self


@dataclass(slots=True)
class MemoryRecord:
    memory_id: str = field(default_factory=lambda: uuid4().hex)
    user_id: str = ""
    memory_type: MemoryType | str = MemoryType.PREFERENCE
    key: str = ""
    content: str = ""
    scenario: Scene | str = Scene.GLOBAL
    confidence: float = 0.8
    source: str = "extracted"
    source_events: list[str] = field(default_factory=list)
    source_summaries: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | str = field(default_factory=_utc_now)
    updated_at: datetime | str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        self.memory_type = _coerce_enum(MemoryType, self.memory_type)
        self.scenario = _coerce_enum(Scene, self.scenario)
        self.source_events = _ensure_list(self.source_events)
        self.source_summaries = _ensure_list(self.source_summaries)
        self.tags = _ensure_list(self.tags)
        self.metadata = dict(self.metadata or {})
        self.created_at = _coerce_datetime(self.created_at)
        self.updated_at = _coerce_datetime(self.updated_at)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))

    @classmethod
    def from_candidate(
        cls,
        candidate: MemoryCandidate,
        *,
        memory_id: str | None = None,
        updated_at: datetime | str | None = None,
    ) -> "MemoryRecord":
        return cls(
            memory_id=memory_id or uuid4().hex,
            user_id=candidate.user_id,
            memory_type=candidate.memory_type,
            key=candidate.key,
            content=candidate.content,
            scenario=candidate.scenario,
            confidence=candidate.confidence,
            source=candidate.source,
            source_events=list(candidate.source_events),
            source_summaries=list(candidate.source_summaries),
            tags=list(candidate.tags),
            metadata=dict(candidate.metadata),
            created_at=candidate.created_at,
            updated_at=updated_at or candidate.created_at,
        )

