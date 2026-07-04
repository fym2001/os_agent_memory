"""Shared helpers for extractor implementations.

The concrete extractors keep their public method signatures, while common
normalisation, deterministic IDs, confidence constants, and logger creation live
here to avoid repeated local copies.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from core.constants import MemoryType


DEFAULT_ID_LENGTH = 32
SHORT_ID_LENGTH = 16
HASH_TOKEN_LENGTH = 12

MIN_INFERRED_CONFIDENCE = 0.55
WORKFLOW_BASE_CONFIDENCE = 0.62
LOW_CONFIDENCE_FLOOR = 0.62
SENSITIVE_REDACTED_MAX_CONFIDENCE = 0.72
LLM_NO_EVIDENCE_MAX_CONFIDENCE = 0.68
LLM_DEFAULT_CONFIDENCE = 0.65
LLM_MISSING_EVIDENCE_PENALTY = 0.08
LLM_REASON_BONUS = 0.02
LLM_SCOPE_BONUS = 0.02
LLM_SHORT_TERM_PENALTY = 0.20
HIGH_CONFIDENCE_THRESHOLD = 0.90
MAX_CANDIDATE_CONFIDENCE = 0.95
KNOWLEDGE_GUIDE_BASE_CONFIDENCE = 0.72
TOOL_PATTERN_MAX_CONFIDENCE = 0.98
CORROBORATION_CONFIDENCE_BONUS = 0.05

_SLUG_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")


def slugify(value: Any, *, fallback: str = "memory") -> str:
    token = _SLUG_RE.sub("_", str(value or "").strip().lower()).strip("_")
    token = re.sub(r"_+", "_", token)
    return token or fallback


def stable_candidate_id(
    user_id: str,
    memory_type: MemoryType,
    key: str,
    *,
    length: int = DEFAULT_ID_LENGTH,
) -> str:
    payload = f"{user_id}\x1f{memory_type.value}\x1f{key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def stable_hash_token(value: str, *, length: int = HASH_TOKEN_LENGTH) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def extractor_logger(module_name: str) -> logging.Logger:
    return logging.getLogger(module_name)
