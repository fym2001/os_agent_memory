"""Deterministic lifecycle state validation shared by admission and D-side flows."""

from __future__ import annotations

from core.constants import MemoryStatus


class InvalidMemoryTransition(ValueError):
    """Raised when a memory lifecycle transition is not allowed."""


_ALLOWED_TRANSITIONS: dict[MemoryStatus, frozenset[MemoryStatus]] = {
    MemoryStatus.PENDING: frozenset(
        {MemoryStatus.ACTIVE, MemoryStatus.REJECTED, MemoryStatus.DELETED}
    ),
    MemoryStatus.ACTIVE: frozenset(
        {
            MemoryStatus.SUPERSEDED,
            MemoryStatus.ARCHIVED,
            MemoryStatus.EXPIRED,
            MemoryStatus.DELETED,
        }
    ),
    MemoryStatus.SUPERSEDED: frozenset(
        {MemoryStatus.ARCHIVED, MemoryStatus.DELETED}
    ),
    MemoryStatus.ARCHIVED: frozenset(
        {MemoryStatus.ACTIVE, MemoryStatus.DELETED}
    ),
    MemoryStatus.EXPIRED: frozenset(
        {MemoryStatus.ARCHIVED, MemoryStatus.DELETED}
    ),
    MemoryStatus.REJECTED: frozenset({MemoryStatus.DELETED}),
    MemoryStatus.DELETED: frozenset(),
}


def can_transition(current: MemoryStatus, target: MemoryStatus) -> bool:
    """Return whether a transition is valid; same-state writes are idempotent."""

    return current == target or target in _ALLOWED_TRANSITIONS[current]


def ensure_transition_allowed(
    current: MemoryStatus,
    target: MemoryStatus,
) -> None:
    if not can_transition(current, target):
        raise InvalidMemoryTransition(
            f"memory status cannot transition from {current.value} to {target.value}"
        )
