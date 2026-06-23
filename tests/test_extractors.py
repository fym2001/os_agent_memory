from __future__ import annotations

from datetime import datetime, timezone

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors.knowledge_extractor import KnowledgeExtractor
from extractors.preference_extractor import PreferenceExtractor


def _event(
    event_id: str,
    *,
    user_id: str = "user-1",
    session_id: str = "session-1",
    task_id: str = "task-1",
    event_type: EventType = EventType.CONVERSATION,
    scenario: Scene = Scene.CHAT,
    source: str = "conversation",
    content: str | None = None,
    tool_name: str | None = None,
    input_payload: dict | None = None,
    output_payload: dict | None = None,
    metadata: dict | None = None,
    success: bool | None = True,
    timestamp: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id=user_id,
        session_id=session_id,
        task_id=task_id,
        event_type=event_type,
        scenario=scenario,
        source=source,
        content=content,
        tool_name=tool_name,
        input=input_payload or {},
        output=output_payload or {},
        metadata=metadata or {},
        success=success,
        timestamp=timestamp or datetime(2026, 6, 23, tzinfo=timezone.utc),
        raw_event={"event_id": event_id},
    )


def test_preference_explicit_from_conversation() -> None:
    content = "以后都用 Markdown 输出，回答尽量简洁，先给结论后分析。"

    raw_candidates = PreferenceExtractor.extract_explicit_preference(content)
    assert raw_candidates
    assert any(candidate.key == "preference.output_format.markdown" for candidate in raw_candidates)
    assert all(candidate.user_id == "" for candidate in raw_candidates)

    event = _event("evt-1", content=content)
    candidates = PreferenceExtractor.extract_from_conversation(event)

    keys = {candidate.key for candidate in candidates}
    assert "preference.output_format.markdown" in keys
    assert "preference.response_length.简洁" in keys
    assert all(candidate.user_id == event.user_id for candidate in candidates)
    assert all(event.event_id in candidate.source_events for candidate in candidates)
    assert all(0.5 <= candidate.confidence <= 1.0 for candidate in candidates)


def test_preference_implicit_from_frequency() -> None:
    events = [
        _event(
            "evt-2",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown"},
        ),
        _event(
            "evt-3",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown"},
        ),
        _event(
            "evt-4",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown"},
        ),
    ]

    candidates = PreferenceExtractor.extract_implicit_preference(events)

    assert any(candidate.key.startswith("preference.tool.bash") for candidate in candidates)
    assert any(candidate.key.startswith("preference.parameter.format") for candidate in candidates)
    assert all(candidate.memory_type is MemoryType.PREFERENCE for candidate in candidates)
    assert all(candidate.user_id == "user-1" for candidate in candidates)
    assert all(len(candidate.source_events) >= 3 for candidate in candidates)
    assert all(0.5 <= candidate.confidence <= 1.0 for candidate in candidates)


def test_preference_confidence_score() -> None:
    explicit_event = _event(
        "evt-5",
        content="以后都用 PDF 输出，回答尽量简洁。",
    )
    implicit_events = [
        _event("evt-6", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "pdf"}),
        _event("evt-7", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "pdf"}),
        _event("evt-8", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "pdf"}),
    ]

    explicit_candidate = PreferenceExtractor.extract_from_conversation(explicit_event)[0]
    implicit_candidate = PreferenceExtractor.extract_implicit_preference(implicit_events)[0]

    assert explicit_candidate.confidence > implicit_candidate.confidence
    assert 0.9 <= explicit_candidate.confidence <= 1.0
    assert 0.5 <= implicit_candidate.confidence <= 1.0


def test_knowledge_from_tool_result() -> None:
    complete_event = _event(
        "evt-9",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="etl",
        content="批量导出完成。",
        input_payload={"operation": "batch export", "source": "orders.csv", "format": "xlsx"},
        output_payload={"status": "ok", "rows": 1200, "file": "orders_20260623.xlsx"},
        metadata={"mode": "batch"},
    )
    partial_event = _event(
        "evt-10",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="etl",
        content="批量导出处理中。",
        input_payload={"operation": "batch export"},
        output_payload={},
        metadata={},
    )

    complete_candidates = KnowledgeExtractor.extract_from_tool_result(complete_event)
    partial_candidates = KnowledgeExtractor.extract_from_tool_result(partial_event)

    assert len(complete_candidates) >= 1
    candidate = complete_candidates[0]
    assert candidate.memory_type is MemoryType.KNOWLEDGE
    assert candidate.user_id == complete_event.user_id
    assert candidate.key.startswith("knowledge.tool_case.batch_export.")
    assert "输入" in candidate.content
    assert "输出" in candidate.content
    assert complete_event.event_id in candidate.source_events
    assert candidate.confidence >= 0.8
    assert candidate.confidence > partial_candidates[0].confidence

    faq_event = _event(
        "evt-11",
        content="问题：导出失败怎么办？\n解决方案：先检查文件是否被占用，再重试。\n桌面配置：1. 打开 settings；2. 关闭自动更新；3. 重启。",
    )
    faq_candidates = KnowledgeExtractor.extract_from_conversation(faq_event)
    faq_keys = {item.key for item in faq_candidates}
    assert any(key.startswith("knowledge.faq.") for key in faq_keys)
    assert any(key.startswith("knowledge.system.desktop_config") or key.startswith("knowledge.guide.") for key in faq_keys)


def test_knowledge_template_extraction() -> None:
    events = [
        _event(
            "evt-12",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_001.csv", "format": "xlsx"},
            output_payload={"file": "orders_001.xlsx", "status": "ok"},
        ),
        _event(
            "evt-13",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_002.csv", "format": "xlsx"},
            output_payload={"file": "orders_002.xlsx", "status": "ok"},
        ),
    ]

    candidates = KnowledgeExtractor.extract_templates(events)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.memory_type is MemoryType.TEMPLATE
    assert candidate.key.startswith("template.batch_export.")
    assert set(candidate.source_events) == {"evt-12", "evt-13"}
    assert candidate.confidence >= 0.6


def test_knowledge_deduplication() -> None:
    events = [
        _event(
            "evt-14",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_001.csv", "format": "xlsx"},
            output_payload={"file": "orders_001.xlsx", "status": "ok"},
        ),
        _event(
            "evt-15",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_002.csv", "format": "xlsx"},
            output_payload={"file": "orders_002.xlsx", "status": "ok"},
        ),
        _event(
            "evt-16",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_003.csv", "format": "xlsx"},
            output_payload={"file": "orders_003.xlsx", "status": "ok"},
        ),
    ]

    candidates = KnowledgeExtractor.extract_templates(events)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.key.startswith("template.batch_export.")
    assert set(candidate.source_events) == {"evt-14", "evt-15", "evt-16"}
    assert candidate.confidence >= 0.65

