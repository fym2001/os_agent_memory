"""LLM-enhanced memory extraction pipeline.

This module keeps the Phase 1 deterministic extractors as the baseline and
adds an optional LLM semantic layer.  It is intentionally dependency-free:
callers inject a JSON-capable LLM client, while unit tests use a fake client.

Design references:
- LangMem: expand or consolidate memory state from conversations.
- Mem0: user-scoped, cross-session preference and personalization memory.
- Graphiti: provenance-aware, evolving facts with conflict/invalidation hooks.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from typing import Any, Protocol

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent

from .knowledge_extractor import KnowledgeExtractor
from .preference_extractor import PreferenceExtractor
from .tool_extractor import ToolExtractor
from .workflow_extractor import WorkflowExtractor
from .common import (
    CORROBORATION_CONFIDENCE_BONUS,
    HIGH_CONFIDENCE_THRESHOLD,
    LLM_DEFAULT_CONFIDENCE,
    LLM_MISSING_EVIDENCE_PENALTY,
    LLM_NO_EVIDENCE_MAX_CONFIDENCE,
    LLM_REASON_BONUS,
    LLM_SCOPE_BONUS,
    LLM_SHORT_TERM_PENALTY,
    MIN_INFERRED_CONFIDENCE,
    SENSITIVE_REDACTED_MAX_CONFIDENCE,
    SHORT_ID_LENGTH,
    extractor_logger,
    slugify as _shared_slugify,
    stable_candidate_id as _shared_stable_candidate_id,
)


logger = extractor_logger(__name__)


class LLMJsonClient(Protocol):
    """Small adapter boundary for a JSON-returning LLM provider.

    Production code can wrap OpenAI, local Qwen/Ollama, or a team-provided
    service behind this protocol.  The extractor never imports vendor SDKs.
    """

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        """Return JSON-compatible data matching ``schema`` as closely as possible."""


MEMORY_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "is_memory_worthy",
                    "is_long_term",
                    "memory_type",
                    "category",
                    "content",
                    "confidence",
                    "evidence",
                    "reason",
                ],
                "properties": {
                    "is_memory_worthy": {"type": "boolean"},
                    "is_long_term": {"type": "boolean"},
                    "memory_type": {
                        "type": "string",
                        "enum": [memory_type.value for memory_type in MemoryType],
                    },
                    "category": {"type": "string"},
                    "value": {"type": "string"},
                    "scope": {"type": "string"},
                    "content": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                    "sensitivity": {"type": "string"},
                    "conflicts_with": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


_LLM_SIGNAL_RE = re.compile(
    r"(以后|今后|下次|之后|默认|每次|一直|总是|习惯|偏好|喜欢|更喜欢|不要|别|不想|"
    r"还是|最好|尽量|这种|类似|记住|忘记|以后不|先给|直接|统一|长期|可复用|模板)",
    re.IGNORECASE,
)
_TRANSIENT_RE = re.compile(r"(这次|今天|临时|暂时|本次|先这样|只这一次|当前任务)")
_SECRET_KEY_RE = re.compile(r"(api[_-]?key|password|passwd|secret|token|authorization|cookie|private[_-]?key)", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9]{16,}|Bearer\s+[a-z0-9._-]{12,}|"
    r"[a-z0-9]{24,}\.[a-z0-9._-]{8,})\b"
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


def _stable_id(user_id: str, memory_type: MemoryType, key: str) -> str:
    return _shared_stable_candidate_id(user_id, memory_type, key, length=SHORT_ID_LENGTH)


def _slugify(value: Any, *, fallback: str = "memory") -> str:
    return _shared_slugify(value, fallback=fallback)


def _clamp_confidence(value: Any, *, default: float = LLM_DEFAULT_CONFIDENCE) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(score, 1.0))


def _normalise_memory_type(value: Any) -> MemoryType | None:
    if isinstance(value, MemoryType):
        return value
    try:
        return MemoryType(str(value).strip().lower())
    except ValueError:
        return None


def _redact_text(text: str) -> tuple[str, bool]:
    redacted = _SECRET_VALUE_RE.sub("[REDACTED_SECRET]", text)
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", redacted)
    redacted = _PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    return redacted, redacted != text


def _sanitize_for_prompt(value: Any) -> Any:
    """Remove credential-like fields before sending event data to an LLM."""

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                sanitized[str(key)] = "[REDACTED_SECRET]"
            else:
                sanitized[str(key)] = _sanitize_for_prompt(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_prompt(item) for item in value[:30]]
    if isinstance(value, str):
        return _redact_text(value[:4000])[0]
    return value


def _event_payload(event: MemoryEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "user_id": event.user_id,
        "session_id": event.session_id,
        "task_id": event.task_id,
        "event_type": event.event_type.value,
        "scenario": event.scenario.value,
        "source": event.source,
        "actor": event.actor,
        "content": _sanitize_for_prompt(event.content),
        "tool_name": event.tool_name,
        "input": _sanitize_for_prompt(event.input),
        "output": _sanitize_for_prompt(event.output),
        "success": event.success,
        "metadata": _sanitize_for_prompt(event.metadata),
        "timestamp": event.timestamp.isoformat(),
    }


def _candidate_payload(candidate: MemoryCandidate) -> dict[str, Any]:
    return {
        "memory_type": candidate.memory_type.value,
        "key": candidate.key,
        "content": candidate.content,
        "confidence": candidate.confidence,
        "source": candidate.source,
        "tags": candidate.tags,
        "metadata": candidate.metadata,
    }


def build_memory_extraction_prompt(
    events: list[MemoryEvent],
    *,
    rule_candidates: list[MemoryCandidate] | None = None,
    mode: str = "event",
) -> str:
    """Build a compact prompt for semantic memory extraction.

    The prompt asks the LLM to act as a semantic layer, not as the final writer.
    All returned candidates are later schema-checked, redacted, and merged.
    """

    compact_events = [_event_payload(event) for event in events[:20]]
    compact_rules = [_candidate_payload(candidate) for candidate in (rule_candidates or [])[:20]]
    payload = {
        "mode": mode,
        "events": compact_events,
        "baseline_rule_candidates": compact_rules,
        "instructions": [
            "Extract only reusable memory candidates, not ordinary task text.",
            "Distinguish long-term preference, knowledge, workflow, tool, environment, safety, profile, and template memory.",
            "Mark current-task-only or temporary instructions as is_memory_worthy=false or is_long_term=false.",
            "Use evidence from the event text; do not invent facts.",
            "Return structured JSON only, following the supplied schema.",
        ],
        "memory_policy": {
            "langmem_like": "expand or consolidate memory state from conversation and current memory state",
            "mem0_like": "prefer user-scoped personalization and cross-session preferences",
            "graphiti_like": "preserve provenance and temporal scope for evolving facts",
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def should_call_llm(event: MemoryEvent, rule_candidates: list[MemoryCandidate] | None = None) -> bool:
    """Decide whether the optional semantic layer is useful for this event."""

    text = " ".join(
        part
        for part in [
            event.content or "",
            event.tool_name or "",
            json.dumps(_sanitize_for_prompt(event.input), ensure_ascii=False, sort_keys=True),
            json.dumps(_sanitize_for_prompt(event.output), ensure_ascii=False, sort_keys=True),
            json.dumps(_sanitize_for_prompt(event.metadata), ensure_ascii=False, sort_keys=True),
        ]
        if part
    )
    if not text.strip():
        return False

    if event.event_type in {EventType.DOCUMENT_IMPORT, EventType.TASK_SUMMARY, EventType.USER_FEEDBACK}:
        return True
    if event.event_type is EventType.TOOL_RESULT and len(text) > 120:
        return True

    has_signal = bool(_LLM_SIGNAL_RE.search(text))
    rules = rule_candidates or []
    if not rules:
        return has_signal or len(text) > 240

    high_confidence_rules = [candidate for candidate in rules if candidate.confidence >= HIGH_CONFIDENCE_THRESHOLD]
    if high_confidence_rules and not _TRANSIENT_RE.search(text):
        return False
    return has_signal


class CandidateValidator:
    """Validate and sanitize candidates before downstream storage sees them."""

    def __init__(self, *, min_confidence: float = MIN_INFERRED_CONFIDENCE) -> None:
        self.min_confidence = min_confidence

    def validate(self, candidate: MemoryCandidate) -> MemoryCandidate | None:
        content = candidate.content.strip()
        if not content or len(content) > 2000:
            return None

        metadata = copy.deepcopy(candidate.metadata)
        if metadata.get("is_long_term") is False or metadata.get("is_memory_worthy") is False:
            return None

        if candidate.confidence < self.min_confidence:
            return None

        redacted_content, redacted = _redact_text(content)
        contains_secret_key = _SECRET_KEY_RE.search(content) is not None
        if contains_secret_key and candidate.memory_type is not MemoryType.SAFETY:
            metadata["rejected_reason"] = "credential_like_content"
            return None

        confidence = candidate.confidence
        if redacted:
            metadata["sensitive_redacted"] = True
            confidence = min(confidence, SENSITIVE_REDACTED_MAX_CONFIDENCE)

        evidence = str(metadata.get("evidence") or "")
        if candidate.source.startswith("llm") and not evidence.strip():
            confidence = min(confidence, LLM_NO_EVIDENCE_MAX_CONFIDENCE)

        return replace(
            candidate,
            content=redacted_content,
            confidence=max(0.0, min(confidence, 1.0)),
            metadata=metadata,
        )

    def validate_many(self, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        valid: list[MemoryCandidate] = []
        for candidate in candidates:
            checked = self.validate(candidate)
            if checked is not None:
                valid.append(checked)
        return valid


class CandidateMerger:
    """Merge deterministic rule candidates and LLM semantic candidates."""

    @staticmethod
    def merge(
        rule_candidates: list[MemoryCandidate],
        llm_candidates: list[MemoryCandidate],
    ) -> list[MemoryCandidate]:
        validator = CandidateValidator()
        all_candidates = validator.validate_many(rule_candidates) + validator.validate_many(llm_candidates)
        by_identity: dict[tuple[str, MemoryType, str, Scene], MemoryCandidate] = {}

        for candidate in all_candidates:
            identity = (candidate.user_id, candidate.memory_type, candidate.key, candidate.scenario)
            current = by_identity.get(identity)
            if current is None:
                by_identity[identity] = CandidateMerger._clone_candidate(candidate)
                continue
            by_identity[identity] = CandidateMerger._merge_duplicate(current, candidate)

        merged = list(by_identity.values())
        CandidateMerger._annotate_conflicts(merged)
        merged.sort(key=lambda item: (item.memory_type.value, item.key, -item.confidence))
        return merged

    @staticmethod
    def _clone_candidate(candidate: MemoryCandidate) -> MemoryCandidate:
        return replace(
            candidate,
            source_events=list(dict.fromkeys(candidate.source_events)),
            source_summaries=list(dict.fromkeys(candidate.source_summaries)),
            tags=list(dict.fromkeys(candidate.tags)),
            metadata=copy.deepcopy(candidate.metadata),
        )

    @staticmethod
    def _merge_duplicate(left: MemoryCandidate, right: MemoryCandidate) -> MemoryCandidate:
        left_sources = {left.source, *left.metadata.get("merged_sources", [])}
        right_sources = {right.source, *right.metadata.get("merged_sources", [])}
        sources = sorted(left_sources | right_sources)
        corroborated = any(source.startswith("llm") for source in sources) and any(
            not source.startswith("llm") for source in sources
        )
        metadata = copy.deepcopy(left.metadata)
        metadata.update({k: v for k, v in right.metadata.items() if k not in metadata})
        metadata["merged_sources"] = sources
        metadata["corroborated_by_rule_and_llm"] = corroborated

        confidence = max(left.confidence, right.confidence)
        if corroborated:
            confidence = min(1.0, confidence + CORROBORATION_CONFIDENCE_BONUS)

        content = left.content if left.confidence >= right.confidence else right.content
        return replace(
            left,
            content=content,
            confidence=confidence,
            source="hybrid" if corroborated else left.source,
            source_events=list(dict.fromkeys([*left.source_events, *right.source_events])),
            source_summaries=list(dict.fromkeys([*left.source_summaries, *right.source_summaries])),
            tags=[tag for tag in dict.fromkeys([*left.tags, *right.tags, "hybrid" if corroborated else ""]) if tag],
            metadata=metadata,
        )

    @staticmethod
    def _category_key(candidate: MemoryCandidate) -> str:
        parts = candidate.key.split(".")
        if len(parts) >= 2:
            return ".".join(parts[:2])
        return candidate.key

    @staticmethod
    def _annotate_conflicts(candidates: list[MemoryCandidate]) -> None:
        groups: dict[tuple[str, MemoryType, Scene, str], list[MemoryCandidate]] = {}
        for candidate in candidates:
            group_id = (
                candidate.user_id,
                candidate.memory_type,
                candidate.scenario,
                CandidateMerger._category_key(candidate),
            )
            groups.setdefault(group_id, []).append(candidate)

        for group in groups.values():
            if len(group) <= 1:
                continue
            keys = sorted({candidate.key for candidate in group})
            for candidate in group:
                candidate.metadata["possible_conflict_keys"] = [key for key in keys if key != candidate.key]


class LLMMemoryExtractor:
    """Optional semantic memory extractor backed by an injected LLM client."""

    @staticmethod
    def extract_event(
        event: MemoryEvent,
        llm_client: LLMJsonClient,
        *,
        rule_candidates: list[MemoryCandidate] | None = None,
        mode: str = "event",
    ) -> list[MemoryCandidate]:
        return LLMMemoryExtractor.extract_events(
            [event],
            llm_client,
            rule_candidates=rule_candidates,
            mode=mode,
        )

    @staticmethod
    def extract_events(
        events: list[MemoryEvent],
        llm_client: LLMJsonClient,
        *,
        rule_candidates: list[MemoryCandidate] | None = None,
        mode: str = "events",
    ) -> list[MemoryCandidate]:
        if not events:
            return []
        prompt = build_memory_extraction_prompt(events, rule_candidates=rule_candidates, mode=mode)
        raw = llm_client.complete_json(prompt, MEMORY_EXTRACTION_SCHEMA)
        payloads = LLMMemoryExtractor._candidate_payloads(raw)
        candidates: list[MemoryCandidate] = []
        for payload in payloads:
            candidate = LLMMemoryExtractor._candidate_from_payload(payload, events)
            if candidate is not None:
                candidates.append(candidate)
        validated = CandidateValidator().validate_many(candidates)
        logger.debug(
            "LLMMemoryExtractor.extract_events mode=%s events=%d payloads=%d candidates=%d validated=%d",
            mode,
            len(events),
            len(payloads),
            len(candidates),
            len(validated),
        )
        return validated

    @staticmethod
    def _candidate_payloads(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            candidates = raw.get("candidates", [])
            if isinstance(candidates, list):
                return [item for item in candidates if isinstance(item, dict)]
        return []

    @staticmethod
    def _candidate_from_payload(payload: dict[str, Any], events: list[MemoryEvent]) -> MemoryCandidate | None:
        if payload.get("is_memory_worthy") is False or payload.get("is_long_term") is False:
            return None

        memory_type = _normalise_memory_type(payload.get("memory_type"))
        if memory_type is None:
            return None

        anchor = events[0]
        content = str(payload.get("content") or "").strip()
        if not content:
            return None

        category = _slugify(payload.get("category") or "general")
        value = _slugify(payload.get("value") or content[:48])
        key = f"{memory_type.value}.{category}.{value}"
        confidence = LLMMemoryExtractor._adjust_confidence(payload)
        metadata = {
            "extraction_method": "llm_semantic",
            "schema_version": 1,
            "is_memory_worthy": payload.get("is_memory_worthy", True),
            "is_long_term": payload.get("is_long_term", True),
            "category": category,
            "value": str(payload.get("value") or ""),
            "scope": str(payload.get("scope") or ""),
            "evidence": str(payload.get("evidence") or ""),
            "reason": str(payload.get("reason") or ""),
            "sensitivity": str(payload.get("sensitivity") or "unknown"),
            "conflicts_with": payload.get("conflicts_with") if isinstance(payload.get("conflicts_with"), list) else [],
            "source_event_ids": [event.event_id for event in events],
            "provenance": [
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type.value,
                    "timestamp": event.timestamp.isoformat(),
                }
                for event in events
            ],
        }

        return MemoryCandidate(
            candidate_id=_stable_id(anchor.user_id, memory_type, key),
            user_id=anchor.user_id,
            memory_type=memory_type,
            key=key,
            content=content,
            scenario=anchor.scenario,
            confidence=confidence,
            source="llm_extracted",
            source_events=[event.event_id for event in events],
            source_summaries=[],
            tags=list(dict.fromkeys(["llm", memory_type.value, category])),
            metadata=metadata,
        )

    @staticmethod
    def _adjust_confidence(payload: dict[str, Any]) -> float:
        confidence = _clamp_confidence(payload.get("confidence"), default=LLM_DEFAULT_CONFIDENCE)
        evidence = str(payload.get("evidence") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        scope = str(payload.get("scope") or "").strip()
        if not evidence:
            confidence -= LLM_MISSING_EVIDENCE_PENALTY
        if reason:
            confidence += LLM_REASON_BONUS
        if scope:
            confidence += LLM_SCOPE_BONUS
        if payload.get("is_long_term") is False:
            confidence -= LLM_SHORT_TERM_PENALTY
        return round(max(0.0, min(confidence, 1.0)), 4)


class HybridMemoryExtractor:
    """Rule-first B-side extractor with optional LLM semantic enrichment."""

    @staticmethod
    def extract_from_conversation(
        event: MemoryEvent,
        llm_client: LLMJsonClient | None = None,
    ) -> list[MemoryCandidate]:
        rule_candidates = [
            *PreferenceExtractor.extract_from_conversation(event),
            *KnowledgeExtractor.extract_from_conversation(event),
        ]
        llm_candidates: list[MemoryCandidate] = []
        if llm_client is not None and should_call_llm(event, rule_candidates):
            llm_candidates = LLMMemoryExtractor.extract_event(
                event,
                llm_client,
                rule_candidates=rule_candidates,
                mode="conversation",
            )
        merged = CandidateMerger.merge(rule_candidates, llm_candidates)
        logger.debug(
            "HybridMemoryExtractor.extract_from_conversation event_id=%s rules=%d llm=%d merged=%d",
            event.event_id,
            len(rule_candidates),
            len(llm_candidates),
            len(merged),
        )
        return merged

    @staticmethod
    def extract_from_tool_result(
        event: MemoryEvent,
        llm_client: LLMJsonClient | None = None,
    ) -> list[MemoryCandidate]:
        rule_candidates = KnowledgeExtractor.extract_from_tool_result(event)
        llm_candidates: list[MemoryCandidate] = []
        if llm_client is not None and should_call_llm(event, rule_candidates):
            llm_candidates = LLMMemoryExtractor.extract_event(
                event,
                llm_client,
                rule_candidates=rule_candidates,
                mode="tool_result",
            )
        merged = CandidateMerger.merge(rule_candidates, llm_candidates)
        logger.debug(
            "HybridMemoryExtractor.extract_from_tool_result event_id=%s rules=%d llm=%d merged=%d",
            event.event_id,
            len(rule_candidates),
            len(llm_candidates),
            len(merged),
        )
        return merged

    @staticmethod
    def extract_from_session(
        events: list[MemoryEvent],
        llm_client: LLMJsonClient | None = None,
    ) -> list[MemoryCandidate]:
        rule_candidates = [
            *PreferenceExtractor.extract_implicit_preference(events),
            *KnowledgeExtractor.extract_templates(events),
            *WorkflowExtractor.extract_tool_sequence(events),
            *WorkflowExtractor.extract_multi_step_workflow(events),
            *ToolExtractor.extract_tool_pattern(events),
        ]
        llm_candidates: list[MemoryCandidate] = []
        if llm_client is not None and any(should_call_llm(event, []) for event in events):
            llm_candidates = LLMMemoryExtractor.extract_events(
                events,
                llm_client,
                rule_candidates=rule_candidates,
                mode="session",
            )
        merged = CandidateMerger.merge(rule_candidates, llm_candidates)
        logger.debug(
            "HybridMemoryExtractor.extract_from_session events=%d rules=%d llm=%d merged=%d",
            len(events),
            len(rule_candidates),
            len(llm_candidates),
            len(merged),
        )
        return merged
