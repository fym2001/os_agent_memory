"""Print B-side LLM-only memory extraction input/output.

Run:
    python demo/b_llm_pipeline_demo.py

The demo uses a fake LLM client.  It does not call any external API.
It is designed for screen sharing: it shows the dataset before LLM processing,
the sanitized prompt sent to the model, the model JSON response, and the final
``MemoryCandidate[]`` after validation/conversion.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.constants import EventType, Scene
from core.models import MemoryEvent
from extractors.knowledge_extractor import KnowledgeExtractor
from extractors.llm_memory_extractor import (
    HybridMemoryExtractor,
    LLMMemoryExtractor,
    build_memory_extraction_prompt,
    clear_default_llm_client,
    event_payload_for_demo,
    set_default_llm_client,
)
from extractors.preference_extractor import PreferenceExtractor
from extractors.workflow_extractor import WorkflowExtractor


class FakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(prompt)
        self.calls.append({"request": request, "schema": schema})
        mode = request["mode"]
        if mode.startswith("preference"):
            return {
                "candidates": [
                    _candidate(
                        "preference",
                        "response_order",
                        "conclusion_first",
                        "用户偏好报告类内容先给结论再展开",
                        "这种报告以后别写太散，先给结论再展开。",
                        "LLM 判断这是长期写作偏好。",
                        0.88,
                    )
                ]
            }
        if mode.startswith("knowledge"):
            return {
                "candidates": [
                    _candidate(
                        "knowledge",
                        "tool_case",
                        "batch_export",
                        "批量导出时可复用：输入文件列表，输出合并后的 report.pdf",
                        "工具结果显示已完成 batch export。",
                        "LLM 从工具输入输出中总结可复用知识。",
                        0.84,
                    )
                ]
            }
        if mode.startswith("workflow") or mode == "session":
            return {
                "candidates": [
                    _candidate(
                        "workflow",
                        "report_generation",
                        "conclusion_then_export",
                        "报告生成流程：先给结论，再展开依据，最后导出 PDF",
                        "对话偏好和工具导出结果共同构成流程。",
                        "LLM 将多条事件归纳为可复用流程。",
                        0.86,
                    )
                ]
            }
        return {
            "candidates": [
                _candidate(
                    "preference",
                    "response_order",
                    "conclusion_first",
                    "用户偏好报告类内容先给结论再展开",
                    "这种报告以后别写太散，先给结论再展开。",
                    "LLM 判断这是长期写作偏好。",
                    0.88,
                ),
                _candidate(
                    "knowledge",
                    "tool_case",
                    "batch_export",
                    "批量导出时可复用：输入文件列表，输出合并后的 report.pdf",
                    "工具结果显示已完成 batch export。",
                    "LLM 从工具输入输出中总结可复用知识。",
                    0.84,
                ),
                _candidate(
                    "workflow",
                    "report_generation",
                    "conclusion_then_export",
                    "报告生成流程：先给结论，再展开依据，最后导出 PDF",
                    "对话偏好和工具导出结果共同构成流程。",
                    "LLM 将多条事件归纳为可复用流程。",
                    0.86,
                ),
                {
                    **_candidate(
                        "knowledge",
                        "credential",
                        "api_key",
                        "用户 API key 是 sk-demo-secret",
                        "api_key=sk-demo-secret",
                        "这是敏感凭据，应被校验层拒绝。",
                        0.91,
                    ),
                    "sensitivity": "api_key",
                },
            ]
        }


def _candidate(
    memory_type: str,
    category: str,
    value: str,
    content: str,
    evidence: str,
    reason: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "is_memory_worthy": True,
        "is_long_term": True,
        "memory_type": memory_type,
        "category": category,
        "value": value,
        "scope": "demo",
        "content": content,
        "confidence": confidence,
        "evidence": evidence,
        "reason": reason,
        "sensitivity": "none",
    }


def _event(
    event_id: str,
    event_type: EventType,
    *,
    content: str | None = None,
    tool_name: str | None = None,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id="user-demo",
        session_id="session-demo",
        task_id="task-demo",
        event_type=event_type,
        scenario=Scene.OFFICE,
        source=event_type.value,
        actor="user" if event_type is EventType.CONVERSATION else "tool",
        content=content,
        tool_name=tool_name,
        input=input_payload or {},
        output=output_payload or {},
        metadata=metadata or {},
        success=True if event_type is EventType.TOOL_RESULT else None,
        timestamp=datetime(2026, 7, 5, 19, 0, tzinfo=timezone.utc),
    )


def _print_json(title: str, value: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    events = [
        _event(
            "demo-conv-001",
            EventType.CONVERSATION,
            content="这种报告以后别写太散，先给结论再展开。",
        ),
        _event(
            "demo-tool-001",
            EventType.TOOL_RESULT,
            tool_name="batch_export",
            input_payload={"files": ["a.docx", "b.docx"], "format": "pdf"},
            output_payload={
                "status": "success",
                "file": "report.pdf",
                "api_key": "sk-demo-secret",
            },
        ),
    ]
    client = FakeLLMClient()

    prompt = build_memory_extraction_prompt(events, mode="demo_dataset")
    raw_llm_output = client.complete_json(prompt, {})
    final_candidates = LLMMemoryExtractor.extract_events(events, client, mode="demo_dataset")

    set_default_llm_client(client)
    try:
        legacy_api_outputs = {
            "PreferenceExtractor.extract_from_conversation": [
                candidate.to_dict() for candidate in PreferenceExtractor.extract_from_conversation(events[0])
            ],
            "KnowledgeExtractor.extract_from_tool_result": [
                candidate.to_dict() for candidate in KnowledgeExtractor.extract_from_tool_result(events[1])
            ],
            "WorkflowExtractor.extract_multi_step_workflow": [
                candidate.to_dict() for candidate in WorkflowExtractor.extract_multi_step_workflow(events)
            ],
            "HybridMemoryExtractor.extract_from_session": [
                candidate.to_dict() for candidate in HybridMemoryExtractor.extract_from_session(events)
            ],
        }
    finally:
        clear_default_llm_client()

    _print_json("1. LLM 处理前：原始 MemoryEvent 数据集", [event.to_dict() for event in events])
    _print_json("2. LLM 处理前：实际入模的脱敏 JSON", [event_payload_for_demo(event) for event in events])
    _print_json("3. LLM 请求 prompt 结构", json.loads(prompt))
    _print_json("4. LLM 原始输出 JSON", raw_llm_output)
    _print_json("5. LLM 输出后：校验/脱敏/转换后的 MemoryCandidate[]", [candidate.to_dict() for candidate in final_candidates])
    _print_json("6. 旧 B 侧 API 现在的 LLM-only 输出", legacy_api_outputs)

    print("\n=== 7. 展示结论 ===")
    print("- B 侧旧函数名保留，但内部不再跑正则、关键词、频次统计或长度阈值。")
    print("- LLM 处理前数据是 MemoryEvent / MemoryEvent[]，入模前会脱敏。")
    print("- LLM 输出必须是结构化 JSON，再转换为 MemoryCandidate[]。")
    print("- 敏感凭据候选会在校验层拒绝，因此不会进入最终 MemoryCandidate[]。")


if __name__ == "__main__":
    main()
