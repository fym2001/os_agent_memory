"""Tool memory extraction facade backed entirely by the configured LLM.

The extractor no longer computes tool patterns with counters, hardcoded
success formulas, or parameter-frequency rules.  Tool experience is extracted
from model-produced ``MemoryCandidate`` objects.  The legacy success-rate
method reads a model-provided ``metadata.success_rate`` field when available.
"""

from __future__ import annotations

from core.constants import MemoryType
from core.models import MemoryCandidate, MemoryEvent

from .llm_memory_extractor import extract_candidates_with_default_client


class ToolExtractor:
    @staticmethod
    def calculate_tool_success_rate(tool_name: str, events: list[MemoryEvent]) -> float:
        candidates = extract_candidates_with_default_client(
            events,
            mode=f"tool_success_rate:{tool_name}",
            memory_types={MemoryType.TOOL},
        )
        for candidate in candidates:
            if str(candidate.metadata.get("tool_name", "")).strip().lower() != tool_name.strip().lower():
                continue
            try:
                return max(0.0, min(float(candidate.metadata["success_rate"]), 1.0))
            except (KeyError, TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def extract_tool_pattern(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            events,
            mode="tool_pattern",
            memory_types={MemoryType.TOOL},
        )
