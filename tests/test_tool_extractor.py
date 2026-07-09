from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors.llm_memory_extractor import clear_default_llm_client, set_default_llm_client
from extractors.tool_extractor import ToolExtractor


class ToolFakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(prompt)
        self.calls.append({"request": request, "schema": schema})
        return {
            "candidates": [
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "tool",
                    "category": "success_rate",
                    "value": "backup",
                    "scope": "tool_usage",
                    "content": "backup 工具成功率约 0.67，适合常规备份任务。",
                    "confidence": 0.82,
                    "evidence": "LLM reviewed tool calls and results.",
                    "reason": "LLM 从工具调用轨迹中总结工具经验。",
                    "sensitivity": "none",
                }
            ]
        }


@pytest.fixture()
def tool_client() -> ToolFakeLLMClient:
    client = ToolFakeLLMClient()
    set_default_llm_client(client)
    try:
        yield client
    finally:
        clear_default_llm_client()


def _event(event_id: str, event_type: EventType, tool_name: str, success: bool | None = None) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id="u-tool",
        session_id="s-tool",
        task_id="t-tool",
        event_type=event_type,
        scenario=Scene.SYSTEM,
        source=event_type.value,
        actor="agent",
        tool_name=tool_name,
        success=success,
        timestamp=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
    )


def test_extract_tool_pattern_uses_llm(tool_client: ToolFakeLLMClient) -> None:
    events = [_event("call-1", EventType.TOOL_CALL, "backup"), _event("result-1", EventType.TOOL_RESULT, "backup", True)]

    candidates = ToolExtractor.extract_tool_pattern(events)

    assert tool_client.calls[0]["request"]["mode"] == "tool_pattern"
    assert candidates[0].memory_type is MemoryType.TOOL
    assert candidates[0].key == "tool.success_rate.backup"


def test_calculate_tool_success_rate_reads_llm_metadata(tool_client: ToolFakeLLMClient) -> None:
    class RateClient(ToolFakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            payload = super().complete_json(prompt, schema)
            payload["candidates"][0]["content"] = "backup success rate"
            payload["candidates"][0]["confidence"] = 0.9
            payload["candidates"][0]["metadata"] = {"tool_name": "backup", "success_rate": 2 / 3}
            return payload

    client = RateClient()
    set_default_llm_client(client)
    try:
        rate = ToolExtractor.calculate_tool_success_rate("backup", [_event("result-1", EventType.TOOL_RESULT, "backup", True)])
    finally:
        clear_default_llm_client()

    assert client.calls[0]["request"]["mode"] == "tool_success_rate:backup"
    assert rate == pytest.approx(2 / 3)


def test_tool_extractor_has_no_rule_fallback_without_client() -> None:
    clear_default_llm_client()

    assert ToolExtractor.extract_tool_pattern([_event("result-1", EventType.TOOL_RESULT, "backup", True)]) == []
    assert ToolExtractor.calculate_tool_success_rate("backup", []) == 0.0


def test_calculate_tool_success_rate_returns_zero_when_llm_metadata_is_missing_or_mismatched() -> None:
    class MissingRateClient(ToolFakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            payload = super().complete_json(prompt, schema)
            payload["candidates"][0]["metadata"] = {"tool_name": "other_tool"}
            return payload

    set_default_llm_client(MissingRateClient())
    try:
        assert ToolExtractor.calculate_tool_success_rate("backup", [_event("result-1", EventType.TOOL_RESULT, "backup", True)]) == 0.0
    finally:
        clear_default_llm_client()
