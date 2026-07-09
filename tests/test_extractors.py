from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors.knowledge_extractor import KnowledgeExtractor
from extractors.llm_memory_extractor import clear_default_llm_client, set_default_llm_client
from extractors.preference_extractor import PreferenceExtractor


class RoutingFakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(prompt)
        self.calls.append({"request": request, "schema": schema})
        mode = request["mode"]
        if mode.startswith("preference"):
            return _payload("preference", "output_format", "markdown", "用户偏好 Markdown 输出")
        if mode.startswith("knowledge"):
            return _payload("knowledge", "tool_case", "batch_export", "工具结果可复用为批量导出知识")
        if mode.startswith("template"):
            return _payload("template", "data_processing", "merge_files", "合并文件可复用模板")
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
                "scope": "unit_test",
                "content": content,
                "confidence": 0.86,
                "evidence": "fake LLM evidence",
                "reason": "fake LLM reason",
                "sensitivity": "none",
            }
        ]
    }


def _event(
    event_id: str,
    *,
    event_type: EventType = EventType.CONVERSATION,
    content: str | None = None,
    tool_name: str | None = None,
    output: dict[str, Any] | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id="user-b",
        session_id="session-b",
        task_id="task-b",
        event_type=event_type,
        scenario=Scene.OFFICE,
        source=event_type.value,
        actor="user",
        content=content,
        tool_name=tool_name,
        output=output or {},
        timestamp=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
    )


@pytest.fixture()
def llm_client() -> RoutingFakeLLMClient:
    client = RoutingFakeLLMClient()
    set_default_llm_client(client)
    try:
        yield client
    finally:
        clear_default_llm_client()


def test_preference_explicit_from_conversation_uses_llm(llm_client: RoutingFakeLLMClient) -> None:
    event = _event("evt-pref", content="以后都用 Markdown 输出。")

    candidates = PreferenceExtractor.extract_from_conversation(event)

    assert llm_client.calls[0]["request"]["mode"] == "preference_from_conversation"
    assert candidates[0].memory_type is MemoryType.PREFERENCE
    assert candidates[0].key == "preference.output_format.markdown"


def test_preference_explicit_text_has_no_rule_fallback_without_client() -> None:
    clear_default_llm_client()

    assert PreferenceExtractor.extract_explicit_preference("以后都用 PDF 输出") == []


def test_preference_implicit_from_frequency_is_llm_session_extraction(llm_client: RoutingFakeLLMClient) -> None:
    events = [_event("evt-pref-1", content="第 1 次使用某种格式"), _event("evt-pref-2", content="第 2 次使用某种格式")]

    candidates = PreferenceExtractor.extract_implicit_preference(events)

    assert llm_client.calls[0]["request"]["mode"] == "preference_from_session"
    assert candidates[0].source == "llm_extracted"


def test_knowledge_from_tool_result_uses_llm(llm_client: RoutingFakeLLMClient) -> None:
    event = _event(
        "evt-knowledge",
        event_type=EventType.TOOL_RESULT,
        tool_name="exporter",
        output={"status": "ok", "file": "report.pdf"},
    )

    candidates = KnowledgeExtractor.extract_from_tool_result(event)

    assert llm_client.calls[0]["request"]["mode"] == "knowledge_from_tool_result"
    assert candidates[0].memory_type is MemoryType.KNOWLEDGE
    assert candidates[0].key == "knowledge.tool_case.batch_export"


def test_knowledge_template_extraction_uses_llm(llm_client: RoutingFakeLLMClient) -> None:
    events = [_event("evt-template-1", content="合并 a.csv 和 b.csv"), _event("evt-template-2", content="再次合并文件")]

    candidates = KnowledgeExtractor.extract_templates(events)

    assert llm_client.calls[0]["request"]["mode"] == "template_from_session"
    assert candidates[0].memory_type is MemoryType.TEMPLATE
    assert candidates[0].key == "template.data_processing.merge_files"


def test_knowledge_deduplication_is_done_after_llm_output() -> None:
    duplicate = _payload("knowledge", "tool_case", "batch_export", "工具结果可复用为批量导出知识")
    duplicate["candidates"].append(dict(duplicate["candidates"][0]))

    class DuplicateClient:
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            return duplicate

    set_default_llm_client(DuplicateClient())
    try:
        candidates = KnowledgeExtractor.extract_from_conversation(_event("evt-dup", content="导出成功"))
    finally:
        clear_default_llm_client()

    assert len(candidates) == 1
