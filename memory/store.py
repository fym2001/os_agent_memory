from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from core.models import MemoryCandidate, MemoryRecord


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            memory_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            scenario TEXT NOT NULL,
            confidence REAL NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_events TEXT,
            source_summaries TEXT,
            tags TEXT,
            metadata TEXT
        )
        """
    )


def save_memory(db_path: str | Path, candidate: MemoryCandidate) -> MemoryRecord:
    record = MemoryRecord.from_candidate(candidate)
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO memories (
                memory_id, user_id, memory_type, key, content, scenario,
                confidence, source, created_at, updated_at,
                source_events, source_summaries, tags, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.memory_id,
                record.user_id,
                record.memory_type.value,
                record.key,
                record.content,
                record.scenario.value,
                record.confidence,
                record.source,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
                json.dumps(record.source_events, ensure_ascii=False),
                json.dumps(record.source_summaries, ensure_ascii=False),
                json.dumps(record.tags, ensure_ascii=False),
                json.dumps(record.metadata, ensure_ascii=False),
            ),
        )
        connection.commit()

    return record

