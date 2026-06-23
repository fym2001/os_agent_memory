from __future__ import annotations

from datetime import datetime, timezone

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors.knowledge_extractor import KnowledgeExtractor


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


def test_extract_from_tool_result_builds_case_with_input_and_output() -> None:
    event = _event(
        "evt-k1",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="etl",
        content="批量导出完成。",
        input_payload={"operation": "batch export", "source": "orders.csv", "format": "xlsx"},
        output_payload={"status": "ok", "rows": 1200, "file": "orders_20260623.xlsx"},
        metadata={"mode": "batch"},
    )

    candidates = KnowledgeExtractor.extract_from_tool_result(event)

    assert len(candidates) >= 1
    candidate = candidates[0]
    assert candidate.memory_type is MemoryType.KNOWLEDGE
    assert candidate.user_id == event.user_id
    assert candidate.key.startswith("knowledge.tool_case.batch_export.")
    assert "输入" in candidate.content
    assert "输出" in candidate.content
    assert event.event_id in candidate.source_events
    assert candidate.confidence >= 0.8


def test_extract_from_conversation_recognizes_faq_and_guide() -> None:
    event = _event(
        "evt-k2",
        content="问题：导出失败怎么办？\n解决方案：先检查文件是否被占用，再重试。\n桌面配置：1. 打开 settings；2. 关闭自动更新；3. 重启。",
    )

    candidates = KnowledgeExtractor.extract_from_conversation(event)

    keys = {candidate.key for candidate in candidates}
    assert any(key.startswith("knowledge.faq.") for key in keys)
    assert any(key.startswith("knowledge.system.desktop_config") or key.startswith("knowledge.guide.") for key in keys)
    assert all(candidate.memory_type is MemoryType.KNOWLEDGE for candidate in candidates)
    assert all(event.event_id in candidate.source_events for candidate in candidates)


def test_extract_templates_deduplicates_same_template() -> None:
    events = [
        _event(
            "evt-k3",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_001.csv", "format": "xlsx"},
            output_payload={"file": "orders_001.xlsx", "status": "ok"},
        ),
        _event(
            "evt-k4",
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
    assert set(candidate.source_events) == {"evt-k3", "evt-k4"}
    assert candidate.confidence >= 0.6


def test_tool_result_confidence_tracks_completeness() -> None:
    complete_event = _event(
        "evt-k5",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="installer",
        content="软件安装完成。",
        input_payload={"operation": "software setup", "target": "desktop"},
        output_payload={"status": "ok", "package": "app_v1.exe"},
        metadata={"env": "prod"},
    )
    partial_event = _event(
        "evt-k6",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="installer",
        content="安装中。",
        input_payload={"operation": "software setup"},
        output_payload={},
        metadata={},
    )

    complete_candidate = KnowledgeExtractor.extract_from_tool_result(complete_event)[0]
    partial_candidate = KnowledgeExtractor.extract_from_tool_result(partial_event)[0]

    assert complete_candidate.confidence > partial_candidate.confidence
    assert complete_candidate.confidence <= 1.0
    assert partial_candidate.confidence >= 0.5

