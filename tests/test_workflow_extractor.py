from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors.llm_memory_extractor import clear_default_llm_client, set_default_llm_client
from extractors.workflow_extractor import WorkflowExtractor


class WorkflowFakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(prompt)
        self.calls.append({"request": request, "schema": schema})
        if "boundaries" in schema.get("required", []):
            return {"boundaries": [{"start": 0, "end": 2, "reason": "同一工作流"}]}
        return {
            "candidates": [
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "workflow",
                    "category": "monthly_report",
                    "value": "read_generate_export",
                    "scope": "office",
                    "content": "月报流程：读取数据 -> 生成报告 -> 导出 PDF",
                    "confidence": 0.86,
                    "evidence": "LLM reviewed the ordered task events.",
                    "reason": "多个事件共同构成可复用流程。",
                    "sensitivity": "none",
                }
            ]
        }


@pytest.fixture()
def workflow_client() -> WorkflowFakeLLMClient:
    client = WorkflowFakeLLMClient()
    set_default_llm_client(client)
    try:
        yield client
    finally:
        clear_default_llm_client()


def _event(event_id: str, event_type: EventType = EventType.CONVERSATION, tool_name: str | None = None) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id="u-flow",
        session_id="s-flow",
        task_id="t-flow",
        event_type=event_type,
        scenario=Scene.OFFICE,
        source=event_type.value,
        actor="agent",
        content="处理月报" if event_type is EventType.CONVERSATION else None,
        tool_name=tool_name,
        timestamp=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
    )


def test_detect_workflow_boundary_uses_llm(workflow_client: WorkflowFakeLLMClient) -> None:
    events = [_event("e0"), _event("e1", EventType.TOOL_CALL, "read_sheet"), _event("e2", EventType.TOOL_RESULT, "export_pdf")]

    assert WorkflowExtractor.detect_workflow_boundary(events) == [(0, 2)]
    assert workflow_client.calls[0]["request"]["mode"] == "workflow_boundary_detection"


def test_extract_tool_sequence_uses_llm(workflow_client: WorkflowFakeLLMClient) -> None:
    events = [_event("e0"), _event("e1", EventType.TOOL_CALL, "read_sheet"), _event("e2", EventType.TOOL_RESULT, "export_pdf")]

    candidates = WorkflowExtractor.extract_tool_sequence(events)

    assert workflow_client.calls[0]["request"]["mode"] == "workflow_tool_sequence"
    assert candidates[0].memory_type is MemoryType.WORKFLOW
    assert candidates[0].key == "workflow.monthly_report.read_generate_export"


def test_extract_multi_step_workflow_uses_llm(workflow_client: WorkflowFakeLLMClient) -> None:
    candidates = WorkflowExtractor.extract_multi_step_workflow([_event("e0"), _event("e1", EventType.TOOL_CALL, "read_sheet")])

    assert workflow_client.calls[0]["request"]["mode"] == "workflow_multi_step"
    assert candidates[0].source == "llm_extracted"


def test_workflow_extractor_has_no_rule_fallback_without_client() -> None:
    clear_default_llm_client()

    events = [_event("e0"), _event("e1", EventType.TOOL_CALL, "read_sheet")]
    assert WorkflowExtractor.detect_workflow_boundary(events) == []
    assert WorkflowExtractor.extract_tool_sequence(events) == []
    assert WorkflowExtractor.extract_multi_step_workflow(events) == []
