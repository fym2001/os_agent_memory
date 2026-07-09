from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent
from extractors.llm_memory_extractor import (
    CandidateMerger,
    CandidateValidator,
    HybridMemoryExtractor,
    LLMMemoryExtractor,
    LLMWorkflowBoundaryExtractor,
    clear_default_llm_client,
    should_call_llm,
)


class FakeLLMClient:
    def __init__(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        self.calls.append({"prompt": prompt, "schema": schema, "request": json.loads(prompt)})
        return self.payload


def _event(
    event_id: str,
    *,
    event_type: EventType = EventType.CONVERSATION,
    source: str = "conversation",
    content: str | None = None,
    tool_name: str | None = None,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    success: bool | None = True,
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id="user-llm",
        session_id="session-llm",
        task_id="task-llm",
        event_type=event_type,
        scenario=Scene.OFFICE,
        source=source,
        actor="user" if event_type is EventType.CONVERSATION else "agent",
        content=content,
        tool_name=tool_name,
        input=input_payload or {},
        output=output_payload or {},
        metadata=metadata or {},
        success=success,
        timestamp=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
    )


def _payload(
    *,
    memory_type: str = "preference",
    category: str = "response_order",
    value: str = "conclusion_first",
    content: str = "用户偏好报告类内容先给结论再展开",
    confidence: float = 0.84,
    is_memory_worthy: bool = True,
    is_long_term: bool = True,
) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "is_memory_worthy": is_memory_worthy,
                "is_long_term": is_long_term,
                "memory_type": memory_type,
                "category": category,
                "value": value,
                "scope": "report_writing",
                "content": content,
                "confidence": confidence,
                "evidence": "这种报告以后先给结论再展开。",
                "reason": "LLM 判断这是可复用的长期写作偏好。",
                "sensitivity": "none",
            }
        ]
    }


def test_llm_prompt_contains_sanitized_input_dataset_before_model_output() -> None:
    event = _event(
        "evt-before-after-1",
        content="以后报告先给结论。api_key=sk-secret-1234567890",
        metadata={"email": "alice@example.com", "api_key": "sk-secret-1234567890"},
    )
    client = FakeLLMClient(_payload())

    candidates = LLMMemoryExtractor.extract_event(event, client, mode="conversation")

    assert candidates[0].key == "preference.response_order.conclusion_first"
    prompt = client.calls[0]["prompt"]
    request = client.calls[0]["request"]
    assert request["mode"] == "conversation"
    assert request["events"][0]["event_id"] == "evt-before-after-1"
    assert "sk-secret-1234567890" not in prompt
    assert "alice@example.com" not in prompt
    assert "[REDACTED_SECRET]" in prompt
    assert "[REDACTED_EMAIL]" in prompt


def test_hybrid_facade_always_uses_llm_when_client_is_supplied() -> None:
    event = _event("evt-llm-2", content="以后都用 Markdown 输出。")
    client = FakeLLMClient(_payload(category="output_format", value="markdown", content="用户偏好 Markdown 输出"))

    candidates = HybridMemoryExtractor.extract_from_conversation(event, client)

    assert client.calls
    assert [candidate.key for candidate in candidates] == ["preference.output_format.markdown"]
    assert candidates[0].source == "llm_extracted"


def test_should_call_llm_has_no_rule_or_length_gate() -> None:
    assert should_call_llm(_event("evt-empty", content="")) is False
    assert should_call_llm(_event("evt-short", content="好")) is True
    assert should_call_llm(_event("evt-tool", event_type=EventType.TOOL_RESULT, tool_name="export", output_payload={"ok": True})) is True


def test_validator_rejects_temporary_and_credential_like_candidates() -> None:
    event = _event("evt-llm-4", content="这次临时用 PDF，api_key=sk-1234567890abcdef。")
    client = FakeLLMClient(
        {
            "candidates": [
                {
                    "is_memory_worthy": True,
                    "is_long_term": False,
                    "memory_type": "preference",
                    "category": "output_format",
                    "value": "pdf",
                    "scope": "current_task",
                    "content": "用户这次临时使用 PDF",
                    "confidence": 0.9,
                    "evidence": "这次临时用 PDF",
                    "reason": "当前任务约束，不是长期偏好。",
                    "sensitivity": "none",
                },
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "knowledge",
                    "category": "credential",
                    "value": "api_key",
                    "scope": "tool_runtime",
                    "content": "用户 API_KEY 是 sk-1234567890abcdef",
                    "confidence": 0.88,
                    "evidence": "api_key=sk-1234567890abcdef",
                    "reason": "包含凭据，不应进入长期记忆。",
                    "sensitivity": "api_key",
                },
            ]
        }
    )

    candidates = LLMMemoryExtractor.extract_event(event, client)

    assert candidates == []
    assert "sk-1234567890abcdef" not in client.calls[0]["prompt"]


def test_validator_redacts_contact_information_without_changing_extraction_logic() -> None:
    candidate = MemoryCandidate(
        candidate_id="contact-1",
        user_id="user-llm",
        memory_type=MemoryType.SAFETY,
        key="safety.contact.redaction",
        content="不要长期保存 13812345678 和 alice@example.com",
        scenario=Scene.OFFICE,
        confidence=0.91,
        source="llm_extracted",
        metadata={"evidence": "用户要求不要保存联系方式"},
    )

    checked = CandidateValidator().validate(candidate)

    assert checked is not None
    assert "[REDACTED_PHONE]" in checked.content
    assert "[REDACTED_EMAIL]" in checked.content
    assert checked.confidence == 0.91
    assert checked.metadata["sensitive_redacted"] is True


def test_merger_deduplicates_and_marks_conflicts_without_rule_boosting() -> None:
    left = MemoryCandidate(
        candidate_id="pref-1",
        user_id="user-llm",
        memory_type=MemoryType.PREFERENCE,
        key="preference.output_format.markdown",
        content="用户偏好 Markdown",
        scenario=Scene.OFFICE,
        confidence=0.8,
        source="llm_extracted",
        metadata={"evidence": "以后用 Markdown"},
    )
    better_duplicate = MemoryCandidate(
        candidate_id="pref-1b",
        user_id="user-llm",
        memory_type=MemoryType.PREFERENCE,
        key="preference.output_format.markdown",
        content="用户强烈偏好 Markdown",
        scenario=Scene.OFFICE,
        confidence=0.9,
        source="llm_extracted",
        metadata={"evidence": "默认用 Markdown"},
    )
    conflict = MemoryCandidate(
        candidate_id="pref-2",
        user_id="user-llm",
        memory_type=MemoryType.PREFERENCE,
        key="preference.output_format.pdf",
        content="用户偏好 PDF",
        scenario=Scene.OFFICE,
        confidence=0.83,
        source="llm_extracted",
        metadata={"evidence": "以后用 PDF"},
    )

    merged = CandidateMerger.merge([left], [better_duplicate, conflict])

    assert len(merged) == 2
    markdown = next(candidate for candidate in merged if candidate.key.endswith("markdown"))
    assert markdown.content == "用户强烈偏好 Markdown"
    for candidate in merged:
        assert candidate.metadata["possible_conflict_keys"]


def test_workflow_boundaries_are_model_output_not_local_markers() -> None:
    events = [
        _event("evt-0", content="开始处理月报"),
        _event("evt-1", event_type=EventType.TOOL_RESULT, tool_name="read_sheet", output_payload={"rows": 20}),
        _event("evt-2", event_type=EventType.TOOL_RESULT, tool_name="export_pdf", output_payload={"file": "report.pdf"}),
    ]
    client = FakeLLMClient({"boundaries": [{"start": 0, "end": 2, "reason": "同一月报流程"}]})

    assert LLMWorkflowBoundaryExtractor.detect_boundaries(events, client) == [(0, 2)]


def test_llm_parser_handles_empty_events_inactive_events_invalid_types_and_list_shape() -> None:
    client = FakeLLMClient([])
    assert LLMMemoryExtractor.extract_events([], client) == []
    assert LLMMemoryExtractor.extract_event(_event("evt-inactive"), client) == []

    event = _event("evt-list", content="以后报告按模板整理")
    client = FakeLLMClient(
        [
            {
                "is_memory_worthy": True,
                "is_long_term": True,
                "memory_type": "not_a_type",
                "category": "x",
                "value": "x",
                "content": "invalid",
                "confidence": 1,
                "evidence": "invalid",
                "reason": "invalid",
            },
            {
                "is_memory_worthy": True,
                "is_long_term": True,
                "memory_type": "preference",
                "category": "report_style",
                "value": "template",
                "content": "",
                "confidence": 1,
                "evidence": "empty",
                "reason": "empty",
            },
            {
                "is_memory_worthy": True,
                "is_long_term": True,
                "memory_type": "preference",
                "category": "report_style",
                "value": "template",
                "content": "用户偏好按模板整理报告",
                "confidence": "bad-score",
                "evidence": "以后报告按模板整理",
                "reason": "LLM 判断为长期偏好",
            },
        ]
    )

    candidates = LLMMemoryExtractor.extract_event(event, client)

    assert len(candidates) == 1
    assert candidates[0].key == "preference.report_style.template"
    assert candidates[0].confidence == 0.0


def test_hybrid_facade_without_any_client_returns_empty_lists() -> None:
    clear_default_llm_client()
    event = _event("evt-no-client", content="以后先给结论")

    assert HybridMemoryExtractor.extract_from_conversation(event) == []
    assert HybridMemoryExtractor.extract_from_tool_result(event) == []
    assert HybridMemoryExtractor.extract_from_session([event]) == []


def test_boundary_parser_ignores_invalid_model_items() -> None:
    events = [_event("evt-boundary-0", content="开始"), _event("evt-boundary-1", content="结束")]
    client = FakeLLMClient(
        {
            "boundaries": [
                {"start": 0, "end": 1, "reason": "valid"},
                {"start": 1, "end": 5, "reason": "out of range"},
                {"start": "bad", "end": 1, "reason": "bad"},
                "bad",
            ]
        }
    )

    assert LLMWorkflowBoundaryExtractor.detect_boundaries(events, client) == [(0, 1)]
