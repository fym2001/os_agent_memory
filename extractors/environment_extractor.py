"""Environment extraction facade backed entirely by the configured LLM.

Environment memory is no longer inferred with path/category keyword rules.
The tool output is wrapped as a ``SYSTEM_CONTEXT`` event and sent to the
configured LLM client.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent

from .llm_memory_extractor import extract_candidates_with_default_client


def _event_from_tool_output(output: dict) -> MemoryEvent:
    return MemoryEvent(
        event_id=str(output.get("event_id") or "synthetic-environment-output"),
        raw_event_id=str(output.get("raw_event_id") or output.get("event_id") or "synthetic-environment-output"),
        user_id=str(output.get("user_id") or ""),
        session_id=str(output.get("session_id") or ""),
        task_id=str(output.get("task_id") or ""),
        event_type=EventType.SYSTEM_CONTEXT,
        scenario=Scene.SYSTEM,
        source="tool_output",
        actor="system",
        output=output,
        timestamp=datetime.now(timezone.utc),
    )


class EnvironmentExtractor:
    @staticmethod
    def extract_from_tool_output(output: dict) -> list[MemoryCandidate]:
        if not isinstance(output, dict) or not output:
            return []
        return extract_candidates_with_default_client(
            [_event_from_tool_output(output)],
            mode="environment_from_tool_output",
            memory_types={MemoryType.ENVIRONMENT},
        )
