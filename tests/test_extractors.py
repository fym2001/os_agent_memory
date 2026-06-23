from __future__ import annotations

from datetime import datetime, timezone

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
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


def test_extract_explicit_preference_from_conversation() -> None:
    event = _event(
        "evt-1",
        content="以后都用 Markdown 输出，回答尽量简洁，先给结论后分析。",
    )

    candidates = PreferenceExtractor.extract_from_conversation(event)

    keys = {candidate.key for candidate in candidates}
    assert "preference.output_format.markdown" in keys
    assert "preference.response_length.简洁" in keys or "preference.response_style.concise" in keys
    assert any(candidate.memory_type is MemoryType.PREFERENCE for candidate in candidates)
    assert all(candidate.user_id == event.user_id for candidate in candidates)
    assert all(event.event_id in candidate.source_events for candidate in candidates)
    assert all(0.5 <= candidate.confidence <= 1.0 for candidate in candidates)


def test_extract_from_tool_result_uses_structured_output() -> None:
    event = _event(
        "evt-2",
        event_type=EventType.TOOL_RESULT,
        source="tool-result",
        content="工具返回结果。",
        tool_name="exporter",
        output_payload={"preferred_format": "PDF", "preferred_language": "中文"},
        metadata={"style": "简洁"},
    )

    candidates = PreferenceExtractor.extract_from_tool_result(event)

    keys = {candidate.key for candidate in candidates}
    assert "preference.preferred_format.pdf" in keys or "preference.output_format.pdf" in keys
    assert any(candidate.source == "tool_result" for candidate in candidates)
    assert all(candidate.user_id == event.user_id for candidate in candidates)
    assert all(event.event_id in candidate.source_events for candidate in candidates)


def test_extract_implicit_preference_detects_repeated_tool_and_parameter_usage() -> None:
    events = [
        _event("evt-3", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "markdown"}),
        _event("evt-4", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "markdown"}),
        _event("evt-5", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "markdown"}),
    ]

    candidates = PreferenceExtractor.extract_implicit_preference(events)

    assert any(candidate.key.startswith("preference.tool.bash") for candidate in candidates)
    assert any(candidate.key.startswith("preference.parameter.format") for candidate in candidates)
    assert all(candidate.memory_type is MemoryType.PREFERENCE for candidate in candidates)
    assert all(candidate.user_id == "user-1" for candidate in candidates)
    assert all(len(candidate.source_events) >= 3 for candidate in candidates)
    assert all(0.5 <= candidate.confidence <= 1.0 for candidate in candidates)


def test_extract_implicit_preference_detects_repeated_action() -> None:
    events = [
        _event("evt-6", content="每次都先给结论"),
        _event("evt-7", content="每次都先给结论"),
        _event("evt-8", content="每次都先给结论"),
    ]

    candidates = PreferenceExtractor.extract_implicit_preference(events)

    assert any(candidate.key.startswith("preference.workflow.") for candidate in candidates)
    assert any(candidate.source == "implicit_action" for candidate in candidates)
    assert any(candidate.confidence >= 0.55 for candidate in candidates)

