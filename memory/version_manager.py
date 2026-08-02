"""Deterministic identifiers and version calculation for admitted memories."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from core.models import MemoryCandidate, MemoryRecord


def memory_id_for_candidate(
    candidate: MemoryCandidate,
    *,
    content: str | None = None,
) -> str:
    """Return a stable record ID for idempotent retries of the same fact."""

    canonical_content = candidate.content if content is None else content
    payload = "\x1f".join(
        (
            candidate.user_id,
            candidate.candidate_id,
            candidate.memory_type.value,
            candidate.key,
            candidate.scenario.value,
            canonical_content.strip(),
        )
    ).encode("utf-8")
    return f"mem_{hashlib.sha256(payload).hexdigest()[:20]}"


def next_version(
    records: Iterable[MemoryRecord],
    candidate: MemoryCandidate,
) -> int:
    """Calculate the next version inside one user/type/key/scenario stream."""

    versions = [
        record.version
        for record in records
        if record.user_id == candidate.user_id
        and record.memory_type == candidate.memory_type
        and record.key == candidate.key
        and record.scenario == candidate.scenario
    ]
    return max(versions, default=0) + 1
