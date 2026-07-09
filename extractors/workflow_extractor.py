"""Workflow extraction facade backed entirely by the configured LLM.

Workflow detection no longer uses tool-sequence heuristics, transition keyword
markers, file/path regexes, or reproduction-rate formulas.  The configured LLM
must return either workflow candidates or explicit workflow boundaries.
"""

from __future__ import annotations

from core.constants import MemoryType
from core.models import MemoryCandidate, MemoryEvent

from .llm_memory_extractor import detect_boundaries_with_default_client, extract_candidates_with_default_client


class WorkflowExtractor:
    @staticmethod
    def detect_workflow_boundary(events: list[MemoryEvent]) -> list[tuple[int, int]]:
        return detect_boundaries_with_default_client(events)

    @staticmethod
    def extract_tool_sequence(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            events,
            mode="workflow_tool_sequence",
            memory_types={MemoryType.WORKFLOW},
        )

    @staticmethod
    def extract_multi_step_workflow(session_events: list[MemoryEvent]) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            session_events,
            mode="workflow_multi_step",
            memory_types={MemoryType.WORKFLOW},
        )
