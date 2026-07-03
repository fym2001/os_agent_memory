from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent
from extractors.llm_memory_extractor import (
    CandidateMerger,
    CandidateValidator,
    HybridMemoryExtractor,
    LLMMemoryExtractor,
    should_call_llm,
)
from extractors.preference_extractor import PreferenceExtractor


class FakeLLMClient:
    def __init__(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        self.calls.append({"prompt": prompt, "schema": schema})
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
        timestamp=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
    )


def _llm_payload(
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
                "evidence": "这种报告还是先给结论好一点",
                "reason": "用户表达了可复用的后续报告写作偏好",
                "sensitivity": "none",
            }
        ]
    }


def test_hybrid_uses_llm_for_semantic_preference_without_rule_hit() -> None:
    event = _event("evt-llm-1", content="这种报告还是先给结论好一点，再展开细节。")
    client = FakeLLMClient(_llm_payload())

    candidates = HybridMemoryExtractor.extract_from_conversation(event, client)

    assert client.calls
    assert any(candidate.key == "preference.response_order.conclusion_first" for candidate in candidates)
    selected = next(candidate for candidate in candidates if candidate.key == "preference.response_order.conclusion_first")
    assert selected.memory_type is MemoryType.PREFERENCE
    assert selected.source == "llm_extracted"
    assert selected.metadata["extraction_method"] == "llm_semantic"
    assert selected.metadata["scope"] == "report_writing"
    assert event.event_id in selected.source_events


def test_hybrid_skips_llm_for_high_confidence_rule_hit() -> None:
    event = _event("evt-llm-2", content="以后都用 Markdown 输出。")
    client = FakeLLMClient(_llm_payload(value="pdf", content="用户偏好 PDF"))

    candidates = HybridMemoryExtractor.extract_from_conversation(event, client)

    assert client.calls == []
    assert any(candidate.key == "preference.output_format.markdown" for candidate in candidates)
    assert not any(candidate.key == "preference.response_order.pdf" for candidate in candidates)


def test_candidate_merger_deduplicates_and_boosts_rule_llm_agreement() -> None:
    event = _event("evt-llm-3", content="以后都用 Markdown 输出。")
    rule_candidate = PreferenceExtractor.extract_from_conversation(event)[0]
    client = FakeLLMClient(
        _llm_payload(
            category="output_format",
            value="markdown",
            content="用户偏好以后使用 Markdown 输出",
            confidence=0.87,
        )
    )
    llm_candidate = LLMMemoryExtractor.extract_event(event, client)[0]

    merged = CandidateMerger.merge([rule_candidate], [llm_candidate])

    assert len([candidate for candidate in merged if candidate.key == "preference.output_format.markdown"]) == 1
    selected = next(candidate for candidate in merged if candidate.key == "preference.output_format.markdown")
    assert selected.source == "hybrid"
    assert selected.confidence > rule_candidate.confidence
    assert selected.metadata["corroborated_by_rule_and_llm"] is True


def test_llm_rejects_transient_and_credential_like_memory() -> None:
    event = _event("evt-llm-4", content="这次临时用 PDF，api_key=sk-1234567890abcdef1234567890abcdef。")
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
                    "reason": "当前任务约束，不是长期偏好",
                    "sensitivity": "none",
                },
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "knowledge",
                    "category": "credential",
                    "value": "api_key",
                    "scope": "tool_runtime",
                    "content": "用户 API_KEY 是 sk-1234567890abcdef1234567890abcdef",
                    "confidence": 0.88,
                    "evidence": "api_key=sk-1234567890abcdef1234567890abcdef",
                    "reason": "包含凭据，不应进入长期记忆",
                    "sensitivity": "api_key",
                },
            ]
        }
    )

    candidates = LLMMemoryExtractor.extract_event(event, client)

    assert candidates == []
    assert "sk-1234567890abcdef" not in client.calls[0]["prompt"]


def test_llm_session_can_emit_workflow_with_provenance() -> None:
    events = [
        _event("evt-llm-5", content="这种月报流程以后可以复用。"),
        _event(
            "evt-llm-6",
            event_type=EventType.TOOL_CALL,
            source="tool_call",
            tool_name="read_spreadsheet",
            input_payload={"file": "sales.xlsx"},
        ),
        _event(
            "evt-llm-7",
            event_type=EventType.TOOL_RESULT,
            source="tool_result",
            tool_name="export_report",
            output_payload={"file": "report.pdf", "status": "success"},
        ),
    ]
    client = FakeLLMClient(
        _llm_payload(
            memory_type="workflow",
            category="monthly_report",
            value="read_generate_export",
            content="月报流程：读取数据 -> 生成报告 -> 导出 PDF",
            confidence=0.86,
        )
    )

    candidates = HybridMemoryExtractor.extract_from_session(events, client)

    workflow = next(candidate for candidate in candidates if candidate.key == "workflow.monthly_report.read_generate_export")
    assert workflow.memory_type is MemoryType.WORKFLOW
    assert workflow.metadata["provenance"][0]["event_id"] == "evt-llm-5"
    assert workflow.source_events == ["evt-llm-5", "evt-llm-6", "evt-llm-7"]


def test_tool_result_long_output_can_use_llm_semantic_layer() -> None:
    event = _event(
        "evt-llm-8",
        event_type=EventType.TOOL_RESULT,
        source="tool_result",
        tool_name="merge_files",
        input_payload={"files": ["a.csv", "b.csv"], "mode": "dedupe"},
        output_payload={
            "status": "success",
            "summary": "merged two files, removed duplicate rows, exported final_report.csv",
            "rows": 1200,
            "note": "这个流程以后可以作为合并文件模板复用。" * 4,
        },
    )
    client = FakeLLMClient(
        _llm_payload(
            memory_type="template",
            category="data_processing",
            value="merge_files_dedupe",
            content="合并文件模板：读取多个 CSV -> 去重 -> 导出结果文件",
            confidence=0.82,
        )
    )

    candidates = HybridMemoryExtractor.extract_from_tool_result(event, client)

    assert client.calls
    assert any(candidate.key == "template.data_processing.merge_files_dedupe" for candidate in candidates)


def test_validator_redacts_personal_contact_but_keeps_safety_candidate() -> None:
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
    assert checked.confidence == 0.72
    assert checked.metadata["sensitive_redacted"] is True


def test_llm_payload_parser_handles_empty_invalid_and_list_shapes() -> None:
    assert LLMMemoryExtractor.extract_events([], FakeLLMClient(_llm_payload())) == []

    event = _event("evt-llm-9", content="以后按报告模板整理。")
    client = FakeLLMClient(
        [
            {
                "is_memory_worthy": True,
                "is_long_term": True,
                "memory_type": "not_a_type",
                "category": "x",
                "content": "invalid type",
                "confidence": "not-a-number",
                "evidence": "",
                "reason": "",
            },
            {
                "is_memory_worthy": True,
                "is_long_term": True,
                "memory_type": "preference",
                "category": "workflow",
                "value": "report_template",
                "content": "用户偏好报告按模板整理",
                "confidence": "bad-score",
                "evidence": "",
                "reason": "",
            },
        ]
    )

    candidates = LLMMemoryExtractor.extract_event(event, client)

    assert len(candidates) == 1
    assert candidates[0].key == "preference.workflow.report_template"
    assert round(candidates[0].confidence, 2) == 0.57


def test_should_call_llm_handles_empty_and_special_event_types() -> None:
    empty = _event("evt-llm-10", content="")
    assert should_call_llm(empty, []) is False

    feedback = _event(
        "evt-llm-11",
        event_type=EventType.USER_FEEDBACK,
        source="feedback",
        content="这个流程以后不要再这样处理。",
    )
    assert should_call_llm(feedback, []) is True


def test_merger_annotates_possible_conflicts_in_same_category() -> None:
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
    right = MemoryCandidate(
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

    merged = CandidateMerger.merge([left], [right])

    assert len(merged) == 2
    for candidate in merged:
        assert candidate.metadata["possible_conflict_keys"]
