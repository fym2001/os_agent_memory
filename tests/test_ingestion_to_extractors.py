from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors.environment_extractor import EnvironmentExtractor
from extractors.knowledge_extractor import KnowledgeExtractor
from extractors.llm_memory_extractor import clear_default_llm_client, set_default_llm_client
from extractors.preference_extractor import PreferenceExtractor
from extractors.tool_extractor import ToolExtractor


class IntegrationFakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(prompt)
        self.calls.append({"request": request, "schema": schema})
        mode = request["mode"]
        if mode.startswith("preference"):
            return _payload("preference", "output_format", "markdown", "用户偏好 Markdown")
        if mode.startswith("knowledge"):
            return _payload("knowledge", "tool_case", "export_pdf", "PDF 导出工具结果可复用")
        if mode.startswith("tool"):
            return _payload("tool", "experience", "export_pdf", "export_pdf 工具适合文档导出")
        if mode.startswith("environment"):
            return _payload("environment", "path", "documents", "常用文档目录是 /root/docs")
        return {"candidates": []}


def _payload(memory_type: str, category: str, value: str, content: str) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "is_memory_worthy": True,
                "is_long_term": True,
                "memory_type": memory_type,
                "category": category,
                "value": value,
                "scope": "integration",
                "content": content,
                "confidence": 0.84,
                "evidence": "fake integration evidence",
                "reason": "fake integration reason",
                "sensitivity": "none",
            }
        ]
    }


@pytest.fixture()
def integration_client() -> IntegrationFakeLLMClient:
    client = IntegrationFakeLLMClient()
    set_default_llm_client(client)
    try:
        yield client
    finally:
        clear_default_llm_client()


def _event(event_id: str, event_type: EventType, **kwargs: Any) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id="u-ingest",
        session_id="s-ingest",
        task_id="t-ingest",
        event_type=event_type,
        scenario=Scene.OFFICE,
        source=event_type.value,
        actor="user",
        timestamp=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
        **kwargs,
    )


def test_ingestion_output_can_drive_preference_llm_extractor(integration_client: IntegrationFakeLLMClient) -> None:
    event = _event(EventType.CONVERSATION.value, EventType.CONVERSATION, content="以后文档用 Markdown 输出")

    candidates = PreferenceExtractor.extract_from_conversation(event)

    assert integration_client.calls[0]["request"]["events"][0]["event_id"] == EventType.CONVERSATION.value
    assert candidates[0].memory_type is MemoryType.PREFERENCE


def test_tool_result_event_can_drive_knowledge_and_tool_llm_extractors(integration_client: IntegrationFakeLLMClient) -> None:
    event = _event(
        "tool-result",
        EventType.TOOL_RESULT,
        tool_name="export_pdf",
        output={"status": "success", "file": "report.pdf"},
        success=True,
    )

    knowledge = KnowledgeExtractor.extract_from_tool_result(event)
    tool_memory = ToolExtractor.extract_tool_pattern([event])

    assert knowledge[0].memory_type is MemoryType.KNOWLEDGE
    assert tool_memory[0].memory_type is MemoryType.TOOL
    assert [call["request"]["mode"] for call in integration_client.calls] == [
        "knowledge_from_tool_result",
        "tool_pattern",
    ]


def test_environment_metadata_can_drive_environment_llm_extractor(integration_client: IntegrationFakeLLMClient) -> None:
    candidates = EnvironmentExtractor.extract_from_tool_output({"user_id": "u-ingest", "documents": "/root/docs"})

    assert candidates[0].memory_type is MemoryType.ENVIRONMENT
    assert integration_client.calls[0]["request"]["events"][0]["event_type"] == "system_context"
