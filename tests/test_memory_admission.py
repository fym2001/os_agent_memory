"""End-to-end tests for MemoryCandidate -> MemoryRecord admission."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from core.constants import EventType, MemoryStatus, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent
from extractors.llm_memory_extractor import LLMMemoryExtractor
from memory.admission import MemoryAdmissionService, SQLiteAdmissionRepository
from memory.conflict_resolver import ConflictAction, LLMConflictResolver
from memory.lifecycle_state import InvalidMemoryTransition


def candidate(**overrides) -> MemoryCandidate:
    values = {
        "candidate_id": "cand_pdf",
        "user_id": "user-1",
        "memory_type": MemoryType.PREFERENCE,
        "key": "preference.export.format",
        "content": "用户偏好使用 PDF 导出",
        "scenario": Scene.OFFICE,
        "confidence": 0.91,
        "source": "llm_extracted",
        "source_events": ["evt-1"],
        "tags": ["preference", "export"],
        "metadata": {"evidence": "以后都导出 PDF"},
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def response(action: str, **overrides) -> dict:
    value = {
        "action": action,
        "reason": f"model selected {action}",
        "target_memory_ids": [],
        "final_content": "用户偏好使用 PDF 导出",
        "requires_human_review": False,
        "decision_confidence": 0.96,
    }
    value.update(overrides)
    return value


class QueueLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []
        self._lock = threading.Lock()

    def complete_json(self, prompt, schema):
        with self._lock:
            self.prompts.append(prompt)
            item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ConcurrentCreateLLM:
    def __init__(self):
        self.barrier = threading.Barrier(2)
        self.calls = 0
        self._lock = threading.Lock()

    def complete_json(self, prompt, schema):
        with self._lock:
            self.calls += 1
        self.barrier.wait(timeout=5)
        return response("create")


@pytest.fixture
def repository(tmp_path):
    return SQLiteAdmissionRepository(str(tmp_path / "memory.db"))


def service(repository, llm) -> MemoryAdmissionService:
    return MemoryAdmissionService(repository, LLMConflictResolver(llm))


def test_create_promotes_candidate_to_active_record(repository):
    llm = QueueLLM(response("create"))
    result = service(repository, llm).admit(candidate())

    assert result.action is ConflictAction.CREATE
    assert result.status is MemoryStatus.ACTIVE
    assert result.record is not None
    assert result.record.version == 1
    assert result.record.content == "用户偏好使用 PDF 导出"
    stored = repository.list_all()
    assert len(stored) == 1
    assert stored[0].memory_id == result.record.memory_id
    assert stored[0].status is MemoryStatus.ACTIVE


def test_same_candidate_retry_is_idempotent_without_second_llm_call(repository):
    llm = QueueLLM(response("create"))
    admission = service(repository, llm)

    first = admission.admit(candidate())
    second = admission.admit(candidate())

    assert first.record.memory_id == second.record.memory_id
    assert second.action is ConflictAction.DUPLICATE
    assert second.reason == "idempotent_candidate_retry"
    assert len(llm.prompts) == 1
    assert len(repository.list_all()) == 1


def test_semantic_duplicate_reuses_existing_record(repository):
    first_llm = QueueLLM(response("create"))
    first = service(repository, first_llm).admit(candidate())
    duplicate_candidate = candidate(
        candidate_id="cand_pdf_rephrased",
        content="导出文件时优先采用 PDF 格式",
    )
    duplicate_llm = QueueLLM(
        response(
            "duplicate",
            target_memory_ids=[first.record.memory_id],
            final_content="",
        )
    )

    result = service(repository, duplicate_llm).admit(duplicate_candidate)

    assert result.action is ConflictAction.DUPLICATE
    assert result.record.memory_id == first.record.memory_id
    assert len(repository.list_all()) == 1


def test_replace_supersedes_old_record_and_increments_version(repository):
    first = service(repository, QueueLLM(response("create"))).admit(candidate())
    newer = candidate(
        candidate_id="cand_word",
        content="用户现在偏好使用 Word 导出",
    )
    replace_llm = QueueLLM(
        response(
            "replace",
            target_memory_ids=[first.record.memory_id],
            final_content=newer.content,
        )
    )

    result = service(repository, replace_llm).admit(newer)
    records = repository.list_all()

    assert result.action is ConflictAction.REPLACE
    assert result.record.version == 2
    assert result.record.supersedes == first.record.memory_id
    assert [record.status for record in records] == [
        MemoryStatus.SUPERSEDED,
        MemoryStatus.ACTIVE,
    ]


def test_merge_creates_canonical_new_version(repository):
    first = service(repository, QueueLLM(response("create"))).admit(candidate())
    supplement = candidate(
        candidate_id="cand_pdf_quality",
        content="PDF 导出时使用高质量模式",
    )
    merged_content = "用户偏好使用 PDF 导出，并启用高质量模式"
    merge_llm = QueueLLM(
        response(
            "merge",
            target_memory_ids=[first.record.memory_id],
            final_content=merged_content,
        )
    )

    result = service(repository, merge_llm).admit(supplement)

    assert result.action is ConflictAction.MERGE
    assert result.record.content == merged_content
    assert result.record.version == 2
    assert repository.get(first.record.memory_id).status is MemoryStatus.SUPERSEDED


def test_different_scenario_memories_can_coexist(repository):
    office = service(repository, QueueLLM(response("create"))).admit(candidate())
    coding_candidate = candidate(
        candidate_id="cand_markdown",
        scenario=Scene.CODING,
        content="编程文档偏好 Markdown",
    )
    coexist_llm = QueueLLM(
        response("coexist", final_content=coding_candidate.content)
    )

    coding = service(repository, coexist_llm).admit(coding_candidate)

    assert office.record.status is MemoryStatus.ACTIVE
    assert coding.action is ConflictAction.COEXIST
    assert coding.record.version == 1
    assert len(
        repository.list_related(
            coding_candidate,
            statuses=(MemoryStatus.ACTIVE,),
        )
    ) == 2


def test_invalid_model_response_is_persisted_as_pending(repository):
    llm = QueueLLM({"action": "unsafe_unknown_action"})
    result = service(repository, llm).admit(candidate())

    assert result.action is ConflictAction.PENDING
    assert result.status is MemoryStatus.PENDING
    assert result.record.status is MemoryStatus.PENDING
    assert repository.list_all()[0].status is MemoryStatus.PENDING


def test_pending_candidate_can_be_promoted_after_model_recovers(repository):
    admission = service(
        repository,
        QueueLLM({"action": "invalid"}, response("create")),
    )

    pending = admission.admit(candidate())
    promoted = admission.admit(candidate())

    assert pending.status is MemoryStatus.PENDING
    assert promoted.status is MemoryStatus.ACTIVE
    assert promoted.record.memory_id == pending.record.memory_id
    assert len(repository.list_all()) == 1


def test_model_reject_does_not_create_formal_memory(repository):
    reject_llm = QueueLLM(
        response("reject", final_content="", reason="not durable memory")
    )
    result = service(repository, reject_llm).admit(candidate())

    assert result.action is ConflictAction.REJECT
    assert result.status is MemoryStatus.REJECTED
    assert result.record is None
    assert repository.list_all() == []


def test_sensitive_candidate_is_rejected_before_llm(repository):
    llm = QueueLLM(response("create"))
    result = service(repository, llm).admit(
        candidate(content="password=super-secret-value")
    )

    assert result.action is ConflictAction.REJECT
    assert result.reason == "candidate_failed_security_or_completeness_validation"
    assert llm.prompts == []
    assert repository.list_all() == []


def test_llm_outage_fails_closed_without_database_mutation(repository):
    result = service(repository, QueueLLM(RuntimeError("offline"))).admit(candidate())

    assert result.action is ConflictAction.PENDING
    assert result.status is MemoryStatus.PENDING
    assert result.record is None
    assert result.reason == "llm_unavailable:RuntimeError"
    assert repository.list_all() == []


def test_lifecycle_transitions_are_validated(repository):
    admission = service(repository, QueueLLM(response("create")))
    created = admission.admit(candidate()).record

    archived = admission.transition(created.memory_id, MemoryStatus.ARCHIVED)
    restored = admission.transition(created.memory_id, MemoryStatus.ACTIVE)

    assert archived.status is MemoryStatus.ARCHIVED
    assert restored.status is MemoryStatus.ACTIVE
    with pytest.raises(InvalidMemoryTransition):
        admission.transition(created.memory_id, MemoryStatus.REJECTED)


def test_concurrent_same_candidate_creates_only_one_record(repository):
    llm = ConcurrentCreateLLM()
    admission = service(repository, llm)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(admission.admit, [candidate(), candidate()]))

    assert {result.action for result in results} == {
        ConflictAction.CREATE,
        ConflictAction.DUPLICATE,
    }
    assert len(repository.list_all()) == 1
    assert llm.calls == 2


def test_replace_transaction_rolls_back_when_insert_fails(repository, monkeypatch):
    first = service(repository, QueueLLM(response("create"))).admit(candidate())
    newer = candidate(
        candidate_id="cand-word-rollback",
        content="用户偏好使用 Word 导出",
    )
    replace_llm = QueueLLM(
        response(
            "replace",
            target_memory_ids=[first.record.memory_id],
            final_content=newer.content,
        )
    )

    def fail_insert(*args, **kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(repository, "_insert_record", fail_insert)
    with pytest.raises(RuntimeError, match="simulated storage failure"):
        service(repository, replace_llm).admit(newer)

    records = repository.list_all()
    assert len(records) == 1
    assert records[0].memory_id == first.record.memory_id
    assert records[0].status is MemoryStatus.ACTIVE


def test_memory_event_can_flow_through_llm_extraction_into_formal_storage(repository):
    event = MemoryEvent(
        event_id="event-e2e",
        raw_event_id="raw-e2e",
        user_id="user-1",
        session_id="session-e2e",
        task_id="task-e2e",
        event_type=EventType.CONVERSATION,
        scenario=Scene.OFFICE,
        source="conversation",
        actor="user",
        content="以后办公文件统一使用 PDF 导出",
        timestamp=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    extraction_llm = QueueLLM(
        {
            "candidates": [
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "preference",
                    "category": "export",
                    "value": "pdf",
                    "scope": "office",
                    "content": "用户偏好办公文件使用 PDF 导出",
                    "confidence": 0.91,
                    "evidence": event.content,
                    "reason": "用户明确表达了长期偏好",
                    "sensitivity": "none",
                }
            ]
        }
    )
    extracted = LLMMemoryExtractor.extract_event(event, extraction_llm)
    admission_llm = QueueLLM(
        response("create", final_content=extracted[0].content)
    )

    result = service(repository, admission_llm).admit(extracted[0])

    assert len(extracted) == 1
    assert result.status is MemoryStatus.ACTIVE
    assert result.record.memory_type is MemoryType.PREFERENCE
    assert result.record.source_events == ["event-e2e"]
