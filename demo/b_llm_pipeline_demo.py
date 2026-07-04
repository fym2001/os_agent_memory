"""Print a small B-side LLM memory extraction demo.

Run:
    python demo/b_llm_pipeline_demo.py

The demo uses a fake LLM client.  It does not call any external API.
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
from extractors.llm_memory_extractor import CandidateMerger, HybridMemoryExtractor, LLMMemoryExtractor
from extractors.preference_extractor import PreferenceExtractor


class FakeLLMClient:
    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidates": [
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "preference",
                    "category": "response_order",
                    "value": "conclusion_first",
                    "scope": "report_writing",
                    "content": "用户偏好报告类内容先给结论再展开",
                    "confidence": 0.84,
                    "evidence": "这种报告以后别写太散，先给结论再展开。",
                    "reason": "用户表达了后续同类报告写作中的长期偏好",
                    "sensitivity": "none",
                },
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "workflow",
                    "category": "report_generation",
                    "value": "conclusion_then_details",
                    "scope": "report_writing",
                    "content": "报告生成流程：先给结论，再展开依据和细节",
                    "confidence": 0.81,
                    "evidence": "先给结论再展开",
                    "reason": "该表达可归纳为后续报告生成工作流",
                    "sensitivity": "none",
                },
            ]
        }


def _event(event_id: str, content: str) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id="user-demo",
        session_id="session-demo",
        task_id="task-demo",
        event_type=EventType.CONVERSATION,
        scenario=Scene.OFFICE,
        source="conversation",
        actor="user",
        content=content,
        metadata={"window_title": "report assistant"},
        timestamp=datetime(2026, 7, 4, 19, 0, tzinfo=timezone.utc),
    )


def _print_json(title: str, value: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    rule_event = _event("demo-event-rule-001", "以后都用 Markdown 输出。")
    semantic_event = _event("demo-event-llm-001", "这种报告以后别写太散，先给结论再展开。")
    fake_llm = FakeLLMClient()

    rule_candidates = [
        *PreferenceExtractor.extract_from_conversation(rule_event),
        *KnowledgeExtractor.extract_from_conversation(rule_event),
    ]
    llm_candidates = LLMMemoryExtractor.extract_event(
        semantic_event,
        fake_llm,
        rule_candidates=[],
        mode="conversation",
    )
    hybrid_candidates = HybridMemoryExtractor.extract_from_conversation(semantic_event, fake_llm)
    merged_candidates = CandidateMerger.merge(rule_candidates, hybrid_candidates)

    _print_json("1. 规则输入 MemoryEvent", rule_event.to_dict())
    _print_json("2. 语义输入 MemoryEvent", semantic_event.to_dict())
    _print_json("3. 规则抽取结果 rule_candidates", [candidate.to_dict() for candidate in rule_candidates])
    _print_json("4. Fake LLM 抽取结果 llm_candidates", [candidate.to_dict() for candidate in llm_candidates])
    _print_json("5. Hybrid 入口输出 hybrid_candidates", [candidate.to_dict() for candidate in hybrid_candidates])
    _print_json("6. 最终合并输出 MemoryCandidate[]", [candidate.to_dict() for candidate in merged_candidates])

    print("\n=== 7. 演示结论 ===")
    print("- 规则层抽到了明确偏好，例如 Markdown 输出。")
    print("- LLM 层抽到了规则不容易覆盖的语义偏好，例如先给结论再展开。")
    print("- 最终合并结果仍然是 MemoryCandidate[]，后续存储/检索模块可以继续接。")


if __name__ == "__main__":
    main()
