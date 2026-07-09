"""Preference extraction facade backed entirely by the configured LLM client.

Public method names are kept for Phase 1 compatibility.  The implementation no
longer contains regex templates, keyword lists, frequency rules, or length
thresholds.  Callers that still use these legacy signatures must configure a
default LLM client via ``set_default_llm_client`` from
``extractors.llm_memory_extractor``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent

from .llm_memory_extractor import extract_candidates_with_default_client


def _synthetic_conversation_event(content: str) -> MemoryEvent:
    return MemoryEvent(
        event_id="synthetic-preference-content",
        raw_event_id="synthetic-preference-content",
        user_id="",
        session_id="",
        task_id="",
        event_type=EventType.CONVERSATION,
        scenario=Scene.GLOBAL,
        source="conversation",
        actor="user",
        content=content,
        timestamp=datetime.now(timezone.utc),
    )


class PreferenceExtractor:
    @staticmethod
    def extract_from_conversation(event: MemoryEvent) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            [event],
            mode="preference_from_conversation",
            memory_types={MemoryType.PREFERENCE},
        )

    @staticmethod
    def extract_from_tool_result(event: MemoryEvent) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            [event],
            mode="preference_from_tool_result",
            memory_types={MemoryType.PREFERENCE},
        )

    @staticmethod
    def extract_explicit_preference(content: str) -> list[MemoryCandidate]:
        if not content or not content.strip():
            return []
        return extract_candidates_with_default_client(
            [_synthetic_conversation_event(content)],
            mode="preference_from_text",
            memory_types={MemoryType.PREFERENCE},
        )

    @staticmethod
    def extract_implicit_preference(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            events,
            mode="preference_from_session",
            memory_types={MemoryType.PREFERENCE},
        )
