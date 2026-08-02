"""Candidate-to-record admission with semantic conflict decisions.

This module is the write boundary between B-side extractors and long-term
storage.  LLM output decides semantic relationships; deterministic code owns
validation, idempotency, lifecycle transitions, versioning, and SQLite
transactions.
"""

from __future__ import annotations

import copy
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from core.constants import MemoryStatus, MemoryType, Scene
from core.models import MemoryCandidate, MemoryRecord
from extractors.llm_memory_extractor import CandidateValidator

from .conflict_resolver import (
    ConflictAction,
    ConflictDecision,
    LLMConflictResolver,
)
from .lifecycle_state import ensure_transition_allowed
from .store import init_db
from .version_manager import memory_id_for_candidate, next_version


_RECORD_COLUMNS = (
    "memory_id, user_id, memory_type, key, content, scenario, confidence, "
    "version, status, source, created_at, updated_at"
)


class ConcurrentAdmissionError(RuntimeError):
    """The active-memory snapshot changed before the transaction committed."""


@dataclass(frozen=True)
class RepositoryAdmissionOutcome:
    action: ConflictAction
    record: MemoryRecord | None
    previous_records: tuple[MemoryRecord, ...]


@dataclass(frozen=True)
class AdmissionResult:
    """Observable result returned to callers, demos, and audit integrations."""

    candidate_id: str
    action: ConflictAction
    status: MemoryStatus
    reason: str
    record: MemoryRecord | None = None
    previous_records: tuple[MemoryRecord, ...] = ()
    decision: ConflictDecision | None = None
    retry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "action": self.action.value,
            "status": self.status.value,
            "reason": self.reason,
            "record": self.record.to_dict() if self.record else None,
            "previous_records": [record.to_dict() for record in self.previous_records],
            "decision": self.decision.to_dict() if self.decision else None,
            "retry_count": self.retry_count,
        }


class SQLiteAdmissionRepository:
    """Transactional adapter over the existing Phase 0 ``memories`` table.

    No table or column is added.  WAL mode and a busy timeout improve concurrent
    reader/writer behaviour without changing the schema.
    """

    def __init__(self, db_path: str, *, busy_timeout_ms: int = 5000) -> None:
        self.db_path = db_path
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        init_db(db_path)
        self._configure_database()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    def _configure_database(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")

    def get(self, memory_id: str) -> MemoryRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    def list_related(
        self,
        candidate: MemoryCandidate,
        *,
        statuses: Iterable[MemoryStatus] | None = None,
    ) -> list[MemoryRecord]:
        """List the same user/type/key stream across scenarios."""

        with closing(self._connect()) as connection:
            return self._list_related_in_connection(connection, candidate, statuses=statuses)

    def _list_related_in_connection(
        self,
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        *,
        statuses: Iterable[MemoryStatus] | None = None,
    ) -> list[MemoryRecord]:
        query = (
            f"SELECT {_RECORD_COLUMNS} FROM memories "
            "WHERE user_id = ? AND memory_type = ? AND key = ?"
        )
        params: list[object] = [
            candidate.user_id,
            candidate.memory_type.value,
            candidate.key,
        ]
        status_values = [status.value for status in statuses or ()]
        if status_values:
            placeholders = ",".join("?" for _ in status_values)
            query += f" AND status IN ({placeholders})"
            params.extend(status_values)
        query += " ORDER BY scenario, version, created_at, memory_id"
        rows = connection.execute(query, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_all(self, *, user_id: str | None = None) -> list[MemoryRecord]:
        query = f"SELECT {_RECORD_COLUMNS} FROM memories"
        params: tuple[object, ...] = ()
        if user_id is not None:
            query += " WHERE user_id = ?"
            params = (user_id,)
        query += " ORDER BY created_at, memory_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def apply(
        self,
        candidate: MemoryCandidate,
        decision: ConflictDecision,
        *,
        expected_active_ids: tuple[str, ...],
    ) -> RepositoryAdmissionOutcome:
        """Atomically verify the snapshot and apply one validated decision."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            active_records = self._list_related_in_connection(
                connection,
                candidate,
                statuses=(MemoryStatus.ACTIVE,),
            )
            current_ids = tuple(sorted(record.memory_id for record in active_records))
            if current_ids != tuple(sorted(expected_active_ids)):
                raise ConcurrentAdmissionError(
                    "active memory set changed during semantic decision"
                )
            previous_records = tuple(copy.deepcopy(active_records))
            active_ids = {record.memory_id for record in active_records}
            target_ids = set(decision.target_memory_ids)
            if decision.action in {
                ConflictAction.DUPLICATE,
                ConflictAction.MERGE,
                ConflictAction.REPLACE,
            } and (not target_ids or not target_ids.issubset(active_ids)):
                raise ConcurrentAdmissionError(
                    "semantic decision targets are no longer active"
                )

            history = self._list_related_in_connection(connection, candidate)
            base_id = memory_id_for_candidate(candidate)
            base_row = connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM memories WHERE memory_id = ?",
                (base_id,),
            ).fetchone()
            base_record = _row_to_record(base_row) if base_row else None

            if decision.action is ConflictAction.DUPLICATE:
                target = self._target_record(active_records, decision.target_memory_ids[0])
                if base_record and base_record.status is MemoryStatus.PENDING:
                    self._set_status(connection, base_record, MemoryStatus.REJECTED)
                connection.commit()
                return RepositoryAdmissionOutcome(
                    ConflictAction.DUPLICATE,
                    target,
                    previous_records,
                )

            if decision.action is ConflictAction.REJECT:
                rejected = None
                if base_record and base_record.status is MemoryStatus.PENDING:
                    rejected = self._set_status(
                        connection,
                        base_record,
                        MemoryStatus.REJECTED,
                    )
                connection.commit()
                return RepositoryAdmissionOutcome(
                    ConflictAction.REJECT,
                    rejected,
                    previous_records,
                )

            if decision.action is ConflictAction.PENDING:
                if base_record:
                    connection.commit()
                    return RepositoryAdmissionOutcome(
                        ConflictAction.PENDING,
                        base_record,
                        previous_records,
                    )
                pending = self._insert_record(
                    connection,
                    candidate,
                    memory_id=base_id,
                    content=candidate.content,
                    version=next_version(history, candidate),
                    status=MemoryStatus.PENDING,
                )
                connection.commit()
                return RepositoryAdmissionOutcome(
                    ConflictAction.PENDING,
                    pending,
                    previous_records,
                )

            final_content = decision.final_content.strip()
            final_id = memory_id_for_candidate(candidate, content=final_content)
            final_row = connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM memories WHERE memory_id = ?",
                (final_id,),
            ).fetchone()
            final_record = _row_to_record(final_row) if final_row else None

            if final_record and final_record.status is not MemoryStatus.PENDING:
                connection.commit()
                return RepositoryAdmissionOutcome(
                    ConflictAction.DUPLICATE,
                    final_record,
                    previous_records,
                )

            if decision.action in {ConflictAction.MERGE, ConflictAction.REPLACE}:
                for record in active_records:
                    if record.memory_id in target_ids:
                        self._set_status(connection, record, MemoryStatus.SUPERSEDED)

            if base_record and base_record.status is MemoryStatus.PENDING and base_id != final_id:
                self._set_status(connection, base_record, MemoryStatus.REJECTED)

            if final_record and final_record.status is MemoryStatus.PENDING:
                admitted = self._activate_pending(
                    connection,
                    final_record,
                    candidate,
                    content=final_content,
                )
            else:
                admitted = self._insert_record(
                    connection,
                    candidate,
                    memory_id=final_id,
                    content=final_content,
                    version=next_version(history, candidate),
                    status=MemoryStatus.ACTIVE,
                )

            if decision.action in {ConflictAction.MERGE, ConflictAction.REPLACE}:
                admitted.supersedes = decision.target_memory_ids[0]
            connection.commit()
            return RepositoryAdmissionOutcome(
                decision.action,
                admitted,
                previous_records,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def transition(
        self,
        memory_id: str,
        target_status: MemoryStatus,
    ) -> MemoryRecord:
        """Apply one lifecycle transition under a write transaction."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"memory not found: {memory_id}")
            current = _row_to_record(row)
            ensure_transition_allowed(current.status, target_status)
            updated = self._set_status(connection, current, target_status)
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _target_record(
        records: list[MemoryRecord],
        memory_id: str,
    ) -> MemoryRecord:
        for record in records:
            if record.memory_id == memory_id:
                return record
        raise ConcurrentAdmissionError(f"target memory is no longer active: {memory_id}")

    @staticmethod
    def _set_status(
        connection: sqlite3.Connection,
        record: MemoryRecord,
        target_status: MemoryStatus,
    ) -> MemoryRecord:
        ensure_transition_allowed(record.status, target_status)
        if record.status == target_status:
            return record
        now = datetime.now()
        deleted_at = now.isoformat() if target_status is MemoryStatus.DELETED else None
        connection.execute(
            "UPDATE memories SET status = ?, updated_at = ?, deleted_at = ? WHERE memory_id = ?",
            (target_status.value, now.isoformat(), deleted_at, record.memory_id),
        )
        record.status = target_status
        record.updated_at = now
        return record

    @staticmethod
    def _insert_record(
        connection: sqlite3.Connection,
        candidate: MemoryCandidate,
        *,
        memory_id: str,
        content: str,
        version: int,
        status: MemoryStatus,
    ) -> MemoryRecord:
        now = datetime.now()
        connection.execute(
            """
            INSERT INTO memories
            (memory_id, user_id, memory_type, key, content, scenario,
             confidence, version, status, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                candidate.user_id,
                candidate.memory_type.value,
                candidate.key,
                content,
                candidate.scenario.value,
                candidate.confidence,
                version,
                status.value,
                candidate.source,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        return MemoryRecord(
            memory_id=memory_id,
            user_id=candidate.user_id,
            memory_type=candidate.memory_type,
            key=candidate.key,
            content=content,
            scenario=candidate.scenario,
            confidence=candidate.confidence,
            version=version,
            status=status,
            source=candidate.source,
            source_events=list(candidate.source_events),
            source_summaries=list(candidate.source_summaries),
            tags=list(candidate.tags),
            metadata=dict(candidate.metadata),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _activate_pending(
        connection: sqlite3.Connection,
        record: MemoryRecord,
        candidate: MemoryCandidate,
        *,
        content: str,
    ) -> MemoryRecord:
        ensure_transition_allowed(record.status, MemoryStatus.ACTIVE)
        now = datetime.now()
        connection.execute(
            """
            UPDATE memories
            SET content = ?, confidence = ?, source = ?, status = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (
                content,
                candidate.confidence,
                candidate.source,
                MemoryStatus.ACTIVE.value,
                now.isoformat(),
                record.memory_id,
            ),
        )
        record.content = content
        record.confidence = candidate.confidence
        record.source = candidate.source
        record.status = MemoryStatus.ACTIVE
        record.updated_at = now
        record.source_events = list(candidate.source_events)
        record.source_summaries = list(candidate.source_summaries)
        record.tags = list(candidate.tags)
        record.metadata = dict(candidate.metadata)
        return record


class MemoryAdmissionService:
    """Public B-side service that promotes candidates into formal records."""

    def __init__(
        self,
        repository: SQLiteAdmissionRepository,
        conflict_resolver: LLMConflictResolver,
        *,
        validator: CandidateValidator | None = None,
        max_retries: int = 3,
    ) -> None:
        self.repository = repository
        self.conflict_resolver = conflict_resolver
        self.validator = validator or CandidateValidator()
        self.max_retries = max(1, int(max_retries))

    def admit(self, candidate: MemoryCandidate) -> AdmissionResult:
        checked = self.validator.validate(candidate)
        if checked is None:
            return AdmissionResult(
                candidate_id=candidate.candidate_id,
                action=ConflictAction.REJECT,
                status=MemoryStatus.REJECTED,
                reason="candidate_failed_security_or_completeness_validation",
            )

        existing_id = memory_id_for_candidate(checked)
        existing = self.repository.get(existing_id)
        if existing and existing.status is not MemoryStatus.PENDING:
            return AdmissionResult(
                candidate_id=checked.candidate_id,
                action=ConflictAction.DUPLICATE,
                status=existing.status,
                reason="idempotent_candidate_retry",
                record=existing,
            )

        for retry_count in range(self.max_retries):
            retry_existing = self.repository.get(memory_id_for_candidate(checked))
            if retry_existing and retry_existing.status is not MemoryStatus.PENDING:
                return AdmissionResult(
                    candidate_id=checked.candidate_id,
                    action=ConflictAction.DUPLICATE,
                    status=retry_existing.status,
                    reason="idempotent_candidate_retry",
                    record=retry_existing,
                    retry_count=retry_count,
                )
            active_records = self.repository.list_related(
                checked,
                statuses=(MemoryStatus.ACTIVE,),
            )
            try:
                decision = self.conflict_resolver.resolve(checked, active_records)
            except Exception as exc:
                return AdmissionResult(
                    candidate_id=checked.candidate_id,
                    action=ConflictAction.PENDING,
                    status=MemoryStatus.PENDING,
                    reason=f"llm_unavailable:{type(exc).__name__}",
                    previous_records=tuple(active_records),
                    retry_count=retry_count,
                )

            try:
                outcome = self.repository.apply(
                    checked,
                    decision,
                    expected_active_ids=tuple(
                        record.memory_id for record in active_records
                    ),
                )
            except ConcurrentAdmissionError:
                if retry_count + 1 == self.max_retries:
                    raise
                continue

            status = (
                outcome.record.status
                if outcome.record is not None
                else (
                    MemoryStatus.REJECTED
                    if outcome.action is ConflictAction.REJECT
                    else MemoryStatus.PENDING
                )
            )
            return AdmissionResult(
                candidate_id=checked.candidate_id,
                action=outcome.action,
                status=status,
                reason=decision.reason,
                record=outcome.record,
                previous_records=outcome.previous_records,
                decision=decision,
                retry_count=retry_count,
            )

        raise ConcurrentAdmissionError("memory admission retries exhausted")

    def admit_many(
        self,
        candidates: Iterable[MemoryCandidate],
    ) -> list[AdmissionResult]:
        return [self.admit(candidate) for candidate in candidates]

    def transition(
        self,
        memory_id: str,
        target_status: MemoryStatus,
    ) -> MemoryRecord:
        return self.repository.transition(memory_id, target_status)


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=row["memory_id"],
        user_id=row["user_id"],
        memory_type=MemoryType(row["memory_type"]),
        key=row["key"],
        content=row["content"],
        scenario=Scene(row["scenario"]),
        confidence=row["confidence"],
        version=row["version"],
        status=MemoryStatus(row["status"]),
        source=row["source"] or "extracted",
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
