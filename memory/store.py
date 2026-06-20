"""
SQLite 存储层
"""
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import DEFAULT_DB_PATH, DEFAULT_EVENTS_DB_PATH, MEMORY_STORE_PATH
from core.models import MemoryEvent, MemoryCandidate, MemoryRecord
from core.constants import MemoryStatus, MemoryType, Scene


def init_db(db_path: str) -> None:
    """初始化 SQLite 数据库"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建 memories 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            memory_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            scenario TEXT DEFAULT 'global',
            confidence REAL DEFAULT 0.8,
            version INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        )
    """)

    # 创建 events 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            scenario TEXT NOT NULL,
            source TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            raw_event TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_event(db_path: str, event: MemoryEvent) -> str:
    """保存事件到 SQLite"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    event_id = event.raw_event.event_id
    cursor.execute("""
        INSERT INTO events
        (event_id, user_id, session_id, task_id, event_type, scenario, source, content, timestamp, raw_event)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        event.user_id,
        event.session_id,
        event.task_id,
        event.event_type.value,
        event.scenario.value,
        event.source,
        event.content,
        event.raw_event.timestamp.isoformat(),
        json.dumps(event.raw_event.to_dict()),
    ))

    conn.commit()
    conn.close()
    return event_id


def list_events(db_path: str, user_id: str, task_id: Optional[str] = None) -> list[MemoryEvent]:
    """查询事件"""
    if not Path(db_path).exists():
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    if task_id:
        cursor.execute("SELECT * FROM events WHERE user_id = ? AND task_id = ?", (user_id, task_id))
    else:
        cursor.execute("SELECT * FROM events WHERE user_id = ?", (user_id,))

    rows = cursor.fetchall()
    conn.close()

    # 简化版：只返回原始数据，由调用方处理转换
    return rows


def save_memory(db_path: str, candidate: MemoryCandidate) -> MemoryRecord:
    """保存候选记忆为正式记忆"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    memory_id = f"mem_{uuid.uuid4().hex[:10]}"
    now = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO memories
        (memory_id, user_id, memory_type, key, content, scenario, confidence, version, status, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        memory_id,
        candidate.user_id,
        candidate.memory_type.value,
        candidate.key,
        candidate.content,
        candidate.scenario.value,
        candidate.confidence,
        1,  # version
        MemoryStatus.ACTIVE.value,
        candidate.source,
        now,
        now,
    ))

    conn.commit()
    conn.close()

    return MemoryRecord(
        memory_id=memory_id,
        user_id=candidate.user_id,
        memory_type=candidate.memory_type,
        key=candidate.key,
        content=candidate.content,
        scenario=candidate.scenario,
        confidence=candidate.confidence,
        source=candidate.source,
        created_at=datetime.fromisoformat(now),
        updated_at=datetime.fromisoformat(now),
    )


def list_active_memories(db_path: str, user_id: str) -> list[MemoryRecord]:
    """查询活跃记忆"""
    if not Path(db_path).exists():
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT memory_id, user_id, memory_type, key, content, scenario, confidence, version, status, source, created_at, updated_at
        FROM memories
        WHERE user_id = ? AND status = 'active'
    """, (user_id,))

    rows = cursor.fetchall()
    conn.close()

    records = []
    for row in rows:
        record = MemoryRecord(
            memory_id=row[0],
            user_id=row[1],
            memory_type=MemoryType(row[2]),
            key=row[3],
            content=row[4],
            scenario=Scene(row[5]),
            confidence=row[6],
            version=row[7],
            status=MemoryStatus(row[8]),
            source=row[9],
            created_at=datetime.fromisoformat(row[10]),
            updated_at=datetime.fromisoformat(row[11]),
        )
        records.append(record)

    return records


def mark_memory_deleted(db_path: str, memory_id: str) -> None:
    """标记记忆为已删除"""
    if not Path(db_path).exists():
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    cursor.execute("""
        UPDATE memories
        SET status = ?, deleted_at = ?
        WHERE memory_id = ?
    """, (MemoryStatus.DELETED.value, now, memory_id))

    conn.commit()
    conn.close()
