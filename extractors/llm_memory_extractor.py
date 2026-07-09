"""LLM-only semantic memory extraction pipeline for B-side extractors.

This module is the single semantic extraction path for B-side memory work.
Deterministic regex/keyword extractors are intentionally not used as a
baseline here: production code injects an LLM JSON client, and tests inject a
fake client.  Thin wrappers in ``preference_extractor.py``,
``knowledge_extractor.py``, ``workflow_extractor.py``, ``tool_extractor.py``,
and ``environment_extractor.py`` preserve the original public method
signatures while delegating extraction to this module.

The remaining local logic is not memory extraction logic.  It is limited to:

- prompt packaging from ``MemoryEvent``;
- credential/contact redaction before prompt and after model output;
- schema conversion from model JSON to ``MemoryCandidate``;
- duplicate/conflict handling for already-produced candidates.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from typing import Any, Iterable, Protocol

from core.constants import MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent

from .common import (
    SHORT_ID_LENGTH,
    extractor_logger,
    slugify as _shared_slugify,
    stable_candidate_id as _shared_stable_candidate_id,
)


logger = extractor_logger(__name__)


class LLMJsonClient(Protocol):
    """Adapter boundary for any JSON-returning LLM provider."""

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> Any:
        """Return JSON-compatible data matching ``schema`` as closely as possible."""


_DEFAULT_LLM_CLIENT: LLMJsonClient | None = None


def set_default_llm_client(client: LLMJsonClient | None) -> None:
    """Set the process-local LLM client used by legacy extractor signatures."""

    global _DEFAULT_LLM_CLIENT
    _DEFAULT_LLM_CLIENT = client


def get_default_llm_client() -> LLMJsonClient | None:
    """Return the process-local LLM client, if configured."""

    return _DEFAULT_LLM_CLIENT


def clear_default_llm_client() -> None:
    """Clear the process-local LLM client.

    Tests should call this in teardown to avoid cross-test leakage.
    """

    set_default_llm_client(None)


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
                    "value",
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


WORKFLOW_BOUNDARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["boundaries"],
    "properties": {
        "boundaries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["start", "end", "reason"],
                "properties": {
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 0},
                    "reason": {"type": "string"},
                },
            },
        }
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


def _stable_id(user_id: str, memory_type: MemoryType, key: str) -> str:
    return _shared_stable_candidate_id(user_id, memory_type, key, length=SHORT_ID_LENGTH)


def _slugify(value: Any, *, fallback: str = "memory") -> str:
    return _shared_slugify(value, fallback=fallback)


def _clamp_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(score, 1.0)), 4)


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
    """Remove sensitive values before event data is sent to the LLM."""

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                sanitized[str(key)] = "[REDACTED_SECRET]"
            else:
                sanitized[str(key)] = _sanitize_for_prompt(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_prompt(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_prompt(item) for item in value]
    if isinstance(value, set):
        return [_sanitize_for_prompt(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, str):
        return _redact_text(value)[0]
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


def event_payload_for_demo(event: MemoryEvent) -> dict[str, Any]:
    """Expose sanitized event payload for demos and review artifacts."""

    return _event_payload(event)


def _has_extractable_payload(event: MemoryEvent) -> bool:
    payload = _event_payload(event)
    return any(
        payload.get(field) not in (None, "", {}, [])
        for field in ("content", "tool_name", "input", "output", "metadata")
    )


def build_memory_extraction_prompt(
    events: list[MemoryEvent],
    *,
    rule_candidates: list[MemoryCandidate] | None = None,
    mode: str = "event",
) -> str:
    """Build the LLM extraction request.

    ``rule_candidates`` is accepted only for backward compatibility with older
    call sites.  It is intentionally not included in the prompt because B-side
    extraction is now LLM-only rather than rule-based.
    """

    payload = {
        "mode": mode,
        "events": [_event_payload(event) for event in events],
        "output_contract": {
            "shape": "Return JSON with a candidates array.",
            "candidate_target": "Each candidate must be directly reusable as a MemoryCandidate.",
            "empty_case": "If no reusable long-term memory exists, return {'candidates': []}.",
        },
        "extraction_policy": [
            "Use semantic understanding to decide whether information is long-term memory.",
            "Do not depend on keyword lists, regex templates, text length thresholds, or rule candidates.",
            "Extract preference, knowledge, workflow, template, tool, environment, safety, profile, task_state, or session_summary memory when supported by evidence.",
            "Mark temporary/current-task-only information as is_memory_worthy=false or is_long_term=false.",
            "Never output credentials or raw secrets. Use redacted evidence when needed.",
            "Keep evidence and reason explicit so reviewers can understand why the memory exists.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def build_workflow_boundary_prompt(events: list[MemoryEvent]) -> str:
    payload = {
        "mode": "workflow_boundary_detection",
        "events": [_event_payload(event) for event in events],
        "output_contract": {
            "shape": "Return JSON with a boundaries array.",
            "indexing": "start and end are zero-based inclusive indices in the supplied events array.",
            "empty_case": "If no workflow boundary is present, return {'boundaries': []}.",
        },
        "extraction_policy": [
            "Use semantic understanding of task phases and tool dependencies.",
            "Do not use fixed keyword markers or hardcoded step patterns.",
            "Return only boundaries supported by event evidence.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def should_call_llm(event: MemoryEvent, rule_candidates: list[MemoryCandidate] | None = None) -> bool:
    """Return whether an event contains any payload worth sending to an LLM.

    The decision no longer contains event-type shortcuts, text-length
    thresholds, confidence thresholds, or regex signal checks.  If there is
    observable payload, the LLM is the extractor.
    """

    return _has_extractable_payload(event)


class CandidateValidator:
    """Validate and sanitize LLM-produced candidates."""

    def validate(self, candidate: MemoryCandidate) -> MemoryCandidate | None:
        content = candidate.content.strip()
        if not content:
            return None

        metadata = copy.deepcopy(candidate.metadata)
        if metadata.get("is_long_term") is False or metadata.get("is_memory_worthy") is False:
            return None

        redacted_content, redacted = _redact_text(content)
        contains_secret_key = _SECRET_KEY_RE.search(content) is not None
        if contains_secret_key and candidate.memory_type is not MemoryType.SAFETY:
            return None

        if redacted:
            metadata["sensitive_redacted"] = True

        return replace(
            candidate,
            content=redacted_content,
            confidence=_clamp_confidence(candidate.confidence),
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
    """Merge LLM candidates from multiple passes or scopes."""

    @staticmethod
    def merge(
        rule_candidates: list[MemoryCandidate],
        llm_candidates: list[MemoryCandidate],
    ) -> list[MemoryCandidate]:
        # The parameter name ``rule_candidates`` is kept for compatibility.
        # In the LLM-only design it means "existing candidates".
        validator = CandidateValidator()
        all_candidates = validator.validate_many(rule_candidates) + validator.validate_many(llm_candidates)
        by_identity: dict[tuple[str, MemoryType, str, Scene], MemoryCandidate] = {}

        for candidate in all_candidates:
            identity = (candidate.user_id, candidate.memory_type, candidate.key, candidate.scenario)
            current = by_identity.get(identity)
            if current is None or candidate.confidence > current.confidence:
                by_identity[identity] = CandidateMerger._clone_candidate(candidate)
                continue
            if candidate.confidence == current.confidence:
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
        metadata = copy.deepcopy(left.metadata)
        metadata.update({k: v for k, v in right.metadata.items() if k not in metadata})
        metadata["merged_sources"] = sorted({left.source, right.source, *metadata.get("merged_sources", [])})
        return replace(
            left,
            source_events=list(dict.fromkeys([*left.source_events, *right.source_events])),
            source_summaries=list(dict.fromkeys([*left.source_summaries, *right.source_summaries])),
            tags=list(dict.fromkeys([*left.tags, *right.tags])),
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
    """Semantic memory extractor backed by an injected LLM client."""

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
        active_events = [event for event in events if _has_extractable_payload(event)]
        if not active_events:
            return []

        prompt = build_memory_extraction_prompt(active_events, rule_candidates=rule_candidates, mode=mode)
        raw = llm_client.complete_json(prompt, MEMORY_EXTRACTION_SCHEMA)
        payloads = LLMMemoryExtractor._candidate_payloads(raw)
        candidates: list[MemoryCandidate] = []
        for payload in payloads:
            candidate = LLMMemoryExtractor._candidate_from_payload(payload, active_events)
            if candidate is not None:
                candidates.append(candidate)
        validated = CandidateMerger.merge([], CandidateValidator().validate_many(candidates))
        logger.debug(
            "LLMMemoryExtractor.extract_events mode=%s events=%d payloads=%d candidates=%d validated=%d",
            mode,
            len(active_events),
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
        category = _slugify(payload.get("category") or "general")
        value = _slugify(payload.get("value") or "unspecified")
        if not content:
            return None

        key = f"{memory_type.value}.{category}.{value}"
        extra_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        metadata = {
            "extraction_method": "llm_semantic",
            "schema_version": 2,
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
        metadata.update(extra_metadata)

        return MemoryCandidate(
            candidate_id=_stable_id(anchor.user_id, memory_type, key),
            user_id=anchor.user_id,
            memory_type=memory_type,
            key=key,
            content=content,
            scenario=anchor.scenario,
            confidence=_clamp_confidence(payload.get("confidence")),
            source="llm_extracted",
            source_events=[event.event_id for event in events],
            source_summaries=[str(payload.get("evidence") or "")],
            tags=list(dict.fromkeys(["llm", memory_type.value, category])),
            metadata=metadata,
        )


class LLMWorkflowBoundaryExtractor:
    """LLM-backed workflow boundary detection."""

    @staticmethod
    def detect_boundaries(events: list[MemoryEvent], llm_client: LLMJsonClient) -> list[tuple[int, int]]:
        if not events:
            return []
        prompt = build_workflow_boundary_prompt(events)
        raw = llm_client.complete_json(prompt, WORKFLOW_BOUNDARY_SCHEMA)
        boundaries = raw.get("boundaries", []) if isinstance(raw, dict) else []
        parsed: list[tuple[int, int]] = []
        for item in boundaries:
            if not isinstance(item, dict):
                continue
            try:
                start = int(item["start"])
                end = int(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= start <= end < len(events):
                parsed.append((start, end))
        logger.debug("LLMWorkflowBoundaryExtractor.detect_boundaries events=%d boundaries=%d", len(events), len(parsed))
        return parsed


def extract_candidates_with_default_client(
    events: Iterable[MemoryEvent],
    *,
    mode: str,
    memory_types: set[MemoryType] | None = None,
) -> list[MemoryCandidate]:
    """Run the configured LLM client and optionally filter memory types."""

    client = get_default_llm_client()
    if client is None:
        return []
    candidates = LLMMemoryExtractor.extract_events(list(events), client, mode=mode)
    if memory_types is None:
        return candidates
    return [candidate for candidate in candidates if candidate.memory_type in memory_types]


def detect_boundaries_with_default_client(events: list[MemoryEvent]) -> list[tuple[int, int]]:
    client = get_default_llm_client()
    if client is None:
        return []
    return LLMWorkflowBoundaryExtractor.detect_boundaries(events, client)


class HybridMemoryExtractor:
    """Compatibility name for the LLM-only B-side extractor facade."""

    @staticmethod
    def extract_from_conversation(
        event: MemoryEvent,
        llm_client: LLMJsonClient | None = None,
    ) -> list[MemoryCandidate]:
        client = llm_client or get_default_llm_client()
        if client is None:
            return []
        result = LLMMemoryExtractor.extract_event(event, client, mode="conversation")
        logger.debug("HybridMemoryExtractor.extract_from_conversation event_id=%s candidates=%d", event.event_id, len(result))
        return result

    @staticmethod
    def extract_from_tool_result(
        event: MemoryEvent,
        llm_client: LLMJsonClient | None = None,
    ) -> list[MemoryCandidate]:
        client = llm_client or get_default_llm_client()
        if client is None:
            return []
        result = LLMMemoryExtractor.extract_event(event, client, mode="tool_result")
        logger.debug("HybridMemoryExtractor.extract_from_tool_result event_id=%s candidates=%d", event.event_id, len(result))
        return result

    @staticmethod
    def extract_from_session(
        events: list[MemoryEvent],
        llm_client: LLMJsonClient | None = None,
    ) -> list[MemoryCandidate]:
        client = llm_client or get_default_llm_client()
        if client is None:
            return []
        result = LLMMemoryExtractor.extract_events(events, client, mode="session")
        logger.debug("HybridMemoryExtractor.extract_from_session events=%d candidates=%d", len(events), len(result))
        return result
