"""Screen-share demo for candidate admission and conflict handling.

Run:
    python demo/b_memory_admission_demo.py

The demo uses a fake JSON LLM and a temporary SQLite database.  It makes no
external request and leaves no database file behind.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.constants import MemoryStatus, MemoryType, Scene
from core.models import MemoryCandidate
from memory.admission import MemoryAdmissionService, SQLiteAdmissionRepository
from memory.conflict_resolver import LLMConflictResolver


class DemoAdmissionLLM:
    """Deterministic fake used only to display the production LLM contract."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(prompt)
        candidate = request["candidate"]
        active = request["active_memories"]
        if not active:
            output = {
                "action": "create",
                "reason": "没有同一记忆键的已生效记录，可以首次入库",
                "target_memory_ids": [],
                "final_content": candidate["content"],
                "requires_human_review": False,
                "decision_confidence": 0.96,
            }
        else:
            output = {
                "action": "replace",
                "reason": "新偏好明确更新了同一办公导出设置",
                "target_memory_ids": [active[0]["memory_id"]],
                "final_content": candidate["content"],
                "requires_human_review": False,
                "decision_confidence": 0.94,
            }
        self.calls.append({"request": request, "response": output, "schema": schema})
        return output


def make_candidate(candidate_id: str, content: str) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        user_id="user-demo",
        memory_type=MemoryType.PREFERENCE,
        key="preference.export.format",
        content=content,
        scenario=Scene.OFFICE,
        confidence=0.91,
        source="llm_extracted",
        source_events=[f"event-{candidate_id}"],
        tags=["preference", "export"],
        metadata={"evidence": content},
    )


def print_json(title: str, value: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    fake_llm = DemoAdmissionLLM()
    with tempfile.TemporaryDirectory(prefix="os-memory-admission-") as temp_dir:
        repository = SQLiteAdmissionRepository(str(Path(temp_dir) / "demo.db"))
        admission = MemoryAdmissionService(
            repository,
            LLMConflictResolver(fake_llm),
        )

        original = make_candidate("cand-pdf", "用户偏好办公文件使用 PDF 导出")
        print_json("1. 第一次入库前：MemoryCandidate", original.to_dict())
        created = admission.admit(original)
        print_json("2. 第一次入库：发送给 LLM 的数据", fake_llm.calls[-1]["request"])
        print_json("3. 第一次入库：LLM 原始结构化输出", fake_llm.calls[-1]["response"])
        print_json("4. 第一次入库后：AdmissionResult", created.to_dict())

        changed = make_candidate("cand-word", "用户现在偏好办公文件使用 Word 导出")
        print_json("5. 冲突候选：新的 MemoryCandidate", changed.to_dict())
        replaced = admission.admit(changed)
        print_json("6. 冲突处理：发送给 LLM 的候选和已有记忆", fake_llm.calls[-1]["request"])
        print_json("7. 冲突处理：LLM 原始结构化输出", fake_llm.calls[-1]["response"])
        print_json("8. 冲突处理后：AdmissionResult", replaced.to_dict())
        print_json(
            "9. SQLite 当前全部版本",
            [record.to_dict() for record in repository.list_all(user_id="user-demo")],
        )

        calls_before_retry = len(fake_llm.calls)
        retried = admission.admit(changed)
        print_json("10. 同一候选重试：幂等结果", retried.to_dict())
        print(
            f"LLM 调用次数变化：{calls_before_retry} -> {len(fake_llm.calls)} "
            "（重复请求未再次调用模型）"
        )

        archived = admission.transition(replaced.record.memory_id, MemoryStatus.ARCHIVED)
        restored = admission.transition(archived.memory_id, MemoryStatus.ACTIVE)
        print_json(
            "11. 生命周期演示：ACTIVE -> ARCHIVED -> ACTIVE",
            {
                "archived_status": archived.status.value,
                "restored_status": restored.status.value,
                "memory_id": restored.memory_id,
            },
        )

    print("\n=== 12. 展示结论 ===")
    print("- LLM 决定语义关系：创建、重复、合并、替换、并存、待确认或拒绝。")
    print("- 程序负责结构校验、敏感信息保护、版本号、状态机和 SQLite 事务。")
    print("- 新旧偏好冲突时，新记录变成 version=2，旧记录变成 superseded。")
    print("- 相同候选重复提交不会重复入库，也不会重复调用 LLM。")


if __name__ == "__main__":
    main()
