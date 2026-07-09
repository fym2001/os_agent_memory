"""Shared infrastructure helpers for extractor implementations.

This file intentionally contains no memory extraction rules.  It only provides
deterministic identifiers, key normalization, and logger creation.
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
