"""Tests for the LLM conflict-decision contract."""

from __future__ import annotations

import json
from datetime import datetime

from core.constants import MemoryStatus, MemoryType, Scene
from core.models import MemoryCandidate, MemoryRecord
from memory.conflict_resolver import (
    ConflictAction,
    LLMConflictResolver,
    build_conflict_decision_prompt,
)


def candidate(**overrides) -> MemoryCandidate:
    values = {
        "candidate_id": "cand_pdf",
        "user_id": "user-1",
        "memory_type": MemoryType.PREFERENCE,
        "key": "preference.export.format",
        "content": "用户偏好使用 PDF 导出",
        "scenario": Scene.OFFICE,
        "confidence": 0.9,
        "source": "llm_extracted",
        "metadata": {},
    }
    values.update(overrides)
    return MemoryCandidate(**values)


def record(**overrides) -> MemoryRecord:
    values = {
        "memory_id": "mem_pdf",
        "user_id": "user-1",
        "memory_type": MemoryType.PREFERENCE,
        "key": "preference.export.format",
        "content": "用户偏好使用 PDF 导出",
        "scenario": Scene.OFFICE,
        "confidence": 0.9,
        "version": 1,
        "status": MemoryStatus.ACTIVE,
        "source": "llm_extracted",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    values.update(overrides)
    return MemoryRecord(**values)


def response(action: str, **overrides) -> dict:
    value = {
        "action": action,
        "reason": "semantic decision",
        "target_memory_ids": [],
        "final_content": "用户偏好使用 PDF 导出",
        "requires_human_review": False,
        "decision_confidence": 0.95,
    }
    value.update(overrides)
    return value


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.prompts: list[str] = []

    def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        assert schema["properties"]["action"]["enum"]
        return self.result


def test_prompt_redacts_secret_metadata():
    prompt = build_conflict_decision_prompt(
        candidate(metadata={"api_key": "sk-do-not-send"}),
        [],
    )
    assert "sk-do-not-send" not in prompt
    assert "[REDACTED_SECRET]" in prompt
    assert json.loads(prompt)["mode"] == "memory_admission_conflict_resolution"


def test_prompt_redacts_contact_data():
    prompt = build_conflict_decision_prompt(
        candidate(content="联系 user@example.com 或 13812345678"),
        [],
    )
    assert "user@example.com" not in prompt
    assert "13812345678" not in prompt
    assert "[REDACTED_EMAIL]" in prompt
    assert "[REDACTED_PHONE]" in prompt


def test_resolver_returns_valid_create_decision():
    client = FakeClient(response("create"))
    decision = LLMConflictResolver(client).resolve(candidate(), [])
    assert decision.action is ConflictAction.CREATE
    assert decision.final_content == "用户偏好使用 PDF 导出"
    assert len(client.prompts) == 1


def test_invalid_action_fails_closed_to_pending():
    decision = LLMConflictResolver.parse_decision(
        response("overwrite_everything"),
        candidate(),
        [],
    )
    assert decision.action is ConflictAction.PENDING
    assert decision.requires_human_review is True


def test_unknown_target_fails_closed_to_pending():
    decision = LLMConflictResolver.parse_decision(
        response("replace", target_memory_ids=["does-not-exist"]),
        candidate(),
        [record()],
    )
    assert decision.action is ConflictAction.PENDING
    assert decision.reason == "llm_referenced_unknown_memory"


def test_mutating_decision_requires_target():
    decision = LLMConflictResolver.parse_decision(
        response("merge", target_memory_ids=[]),
        candidate(),
        [record()],
    )
    assert decision.action is ConflictAction.PENDING
    assert decision.reason == "semantic_mutation_requires_a_target"


def test_create_cannot_bypass_same_scene_active_memory():
    decision = LLMConflictResolver.parse_decision(
        response("create"),
        candidate(),
        [record()],
    )
    assert decision.action is ConflictAction.PENDING
    assert decision.reason == "create_would_bypass_existing_same_scope_memory"


def test_different_scene_can_use_coexist():
    existing = record(scenario=Scene.CODING)
    decision = LLMConflictResolver.parse_decision(
        response("coexist"),
        candidate(),
        [existing],
    )
    assert decision.action is ConflictAction.COEXIST


def test_human_review_request_cannot_mutate_storage_directly():
    decision = LLMConflictResolver.parse_decision(
        response("create", requires_human_review=True),
        candidate(),
        [],
    )
    assert decision.action is ConflictAction.PENDING
    assert decision.reason == "llm_requested_human_review"
