"""Knowledge/template extraction facade backed entirely by the configured LLM.

The public API is unchanged, but extraction no longer uses FAQ regexes,
keyword triggers, or repeated-task heuristics.  The configured LLM is
responsible for deciding whether a tool result, conversation, or event batch
contains reusable knowledge or templates.
"""

from __future__ import annotations

from core.constants import MemoryType
from core.models import MemoryCandidate, MemoryEvent

from .llm_memory_extractor import extract_candidates_with_default_client


class KnowledgeExtractor:
    @staticmethod
    def extract_from_tool_result(event: MemoryEvent) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            [event],
            mode="knowledge_from_tool_result",
            memory_types={MemoryType.KNOWLEDGE, MemoryType.TEMPLATE},
        )

    @staticmethod
    def extract_from_conversation(event: MemoryEvent) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            [event],
            mode="knowledge_from_conversation",
            memory_types={MemoryType.KNOWLEDGE, MemoryType.TEMPLATE},
        )

    @staticmethod
    def extract_templates(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        return extract_candidates_with_default_client(
            events,
            mode="template_from_session",
            memory_types={MemoryType.TEMPLATE},
        )
