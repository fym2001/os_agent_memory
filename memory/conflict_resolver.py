"""LLM-backed semantic conflict decisions for memory admission.

The resolver never mutates storage.  It turns a candidate plus the currently
active memories into a validated, structured decision.  Database state
transitions remain deterministic and are handled by ``memory.admission``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from core.models import MemoryCandidate, MemoryRecord


class AdmissionLLMClient(Protocol):
    """Minimal JSON LLM boundary shared by production adapters and test fakes."""

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        ...


class ConflictAction(str, Enum):
    """Supported semantic decisions for a candidate."""

    CREATE = "create"
    DUPLICATE = "duplicate"
    MERGE = "merge"
    REPLACE = "replace"
    COEXIST = "coexist"
    PENDING = "pending"
    REJECT = "reject"


@dataclass(frozen=True)
class ConflictDecision:
    """Validated LLM decision consumed by the deterministic admission layer."""

    action: ConflictAction
    reason: str
    target_memory_ids: tuple[str, ...] = ()
    final_content: str = ""
    requires_human_review: bool = False
    decision_confidence: float | None = None
    raw_response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "target_memory_ids": list(self.target_memory_ids),
            "final_content": self.final_content,
            "requires_human_review": self.requires_human_review,
            "decision_confidence": self.decision_confidence,
        }


CONFLICT_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "action",
        "reason",
        "target_memory_ids",
        "final_content",
        "requires_human_review",
    ],
    "properties": {
        "action": {
            "type": "string",
            "enum": [action.value for action in ConflictAction],
        },
        "reason": {"type": "string"},
        "target_memory_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "final_content": {"type": "string"},
        "requires_human_review": {"type": "boolean"},
        "decision_confidence": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1,
        },
    },
}


_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|password|passwd|secret|token|authorization|cookie|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]+|Bearer\s+[a-z0-9._~+/-]+|"
    r"[a-z0-9_-]{24,}\.[a-z0-9._-]+)\b"
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


def _sanitize_for_llm(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized[str(key)] = (
                "[REDACTED_SECRET]"
                if _SECRET_KEY_RE.search(str(key))
                else _sanitize_for_llm(item)
            )
        return sanitized
    if isinstance(value, set):
        return [_sanitize_for_llm(item) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_llm(item) for item in value]
    if isinstance(value, str):
        sanitized = _SECRET_VALUE_RE.sub("[REDACTED_SECRET]", value)
        sanitized = _EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)
        return _PHONE_RE.sub("[REDACTED_PHONE]", sanitized)
    return value


def build_conflict_decision_prompt(
    candidate: MemoryCandidate,
    active_memories: list[MemoryRecord],
) -> str:
    """Build a provider-neutral prompt containing no semantic shortcut rules."""

    payload = {
        "mode": "memory_admission_conflict_resolution",
        "candidate": _sanitize_for_llm(candidate.to_dict()),
        "active_memories": [
            _sanitize_for_llm(memory.to_dict()) for memory in active_memories
        ],
        "decision_contract": {
            "create": "No equivalent or conflicting active memory exists.",
            "duplicate": "The candidate expresses the same durable fact; do not write a duplicate.",
            "merge": "The candidate and selected memories are complementary; return canonical final_content.",
            "replace": "The candidate contradicts or updates selected memories; return the new canonical content.",
            "coexist": "Both facts should remain active because their scopes or scenarios differ.",
            "pending": "Evidence is insufficient or the decision needs human review.",
            "reject": "The candidate must not become a stored memory.",
        },
        "requirements": [
            "Use semantic meaning and evidence, not text length, keyword lists, or numeric admission thresholds.",
            "Only reference memory IDs included in active_memories.",
            "For duplicate, merge, or replace, target_memory_ids must not be empty.",
            "For create, merge, replace, or coexist, final_content must be the canonical content to store.",
            "Choose pending when the evidence does not support a safe deterministic mutation.",
            "Return JSON only and follow the supplied schema.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class LLMConflictResolver:
    """Ask an injected LLM for a semantic decision and validate its contract."""

    def __init__(self, llm_client: AdmissionLLMClient) -> None:
        self.llm_client = llm_client

    def resolve(
        self,
        candidate: MemoryCandidate,
        active_memories: list[MemoryRecord],
    ) -> ConflictDecision:
        prompt = build_conflict_decision_prompt(candidate, active_memories)
        raw = self.llm_client.complete_json(prompt, CONFLICT_DECISION_SCHEMA)
        return self.parse_decision(raw, candidate, active_memories)

    @staticmethod
    def parse_decision(
        raw: Any,
        candidate: MemoryCandidate,
        active_memories: list[MemoryRecord],
    ) -> ConflictDecision:
        if not isinstance(raw, dict):
            return _pending_decision("llm_response_is_not_an_object", raw)

        try:
            action = ConflictAction(str(raw.get("action", "")).strip().lower())
        except ValueError:
            return _pending_decision("llm_action_is_invalid", raw)

        reason = str(raw.get("reason") or "").strip()
        final_content = str(raw.get("final_content") or "").strip()
        target_value = raw.get("target_memory_ids")
        if not isinstance(target_value, list):
            return _pending_decision("target_memory_ids_must_be_an_array", raw)
        target_ids = tuple(dict.fromkeys(str(item) for item in target_value if str(item)))

        known_ids = {memory.memory_id for memory in active_memories}
        if any(memory_id not in known_ids for memory_id in target_ids):
            return _pending_decision("llm_referenced_unknown_memory", raw)

        if action in {
            ConflictAction.DUPLICATE,
            ConflictAction.MERGE,
            ConflictAction.REPLACE,
        } and not target_ids:
            return _pending_decision("semantic_mutation_requires_a_target", raw)

        if action in {
            ConflictAction.CREATE,
            ConflictAction.MERGE,
            ConflictAction.REPLACE,
            ConflictAction.COEXIST,
        } and not final_content:
            return _pending_decision("storing_action_requires_final_content", raw)

        same_scene_active = any(
            memory.scenario == candidate.scenario for memory in active_memories
        )
        if action is ConflictAction.CREATE and same_scene_active:
            return _pending_decision("create_would_bypass_existing_same_scope_memory", raw)

        decision_confidence: float | None = None
        if raw.get("decision_confidence") is not None:
            try:
                value = float(raw["decision_confidence"])
            except (TypeError, ValueError):
                return _pending_decision("decision_confidence_is_invalid", raw)
            decision_confidence = max(0.0, min(value, 1.0))

        requires_human_review = bool(raw.get("requires_human_review", False))
        if requires_human_review and action not in {
            ConflictAction.PENDING,
            ConflictAction.REJECT,
        }:
            return _pending_decision("llm_requested_human_review", raw)

        return ConflictDecision(
            action=action,
            reason=reason or "llm_decision",
            target_memory_ids=target_ids,
            final_content=final_content,
            requires_human_review=requires_human_review,
            decision_confidence=decision_confidence,
            raw_response=raw,
        )


def _pending_decision(reason: str, raw: Any) -> ConflictDecision:
    return ConflictDecision(
        action=ConflictAction.PENDING,
        reason=reason,
        requires_human_review=True,
        raw_response=raw,
    )
