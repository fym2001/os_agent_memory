"""
collector.py — JSONL 加载与原始事件解析

数据流起点：
Agent Raw Payload (JSONL)
  → load_jsonl (逐行加载)
  → parse_raw_event (封装为 RawEvent)
  → collect (聚合入口)

返回结果包含统计信息，方便调用方判断数据质量。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.constants import EventType, Scene
from core.models import RawEvent


# ── 收集结果 ────────────────────────────────────────────────────────


@dataclass
class CollectResult:
    """collect() 的返回结果，包含成功事件列表和统计信息。"""

    events: list[RawEvent]
    total: int = 0
    success: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        """是否全部成功（无跳过、无错误）。"""
        return self.skipped == 0 and len(self.errors) == 0


# ── 底层加载 ────────────────────────────────────────────────────────


def load_jsonl(file_path: str | Path, skip_invalid: bool = False) -> list[dict[str, Any]]:
    """
    加载 JSONL 文件，返回原始 dict 列表。

    Parameters
    ----------
    file_path : str | Path
        JSONL 文件路径。
    skip_invalid : bool
        为 True 时静默跳过无效行；为 False（默认）时直接抛出异常。

    Returns
    -------
    list[dict[str, Any]]
        解析后的 JSON 对象列表。

    Raises
    ------
    FileNotFoundError
        文件不存在。
    ValueError
        skip_invalid=False 且遇到无效 JSON 行或非 dict 对象时。
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    events: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                if skip_invalid:
                    continue
                raise ValueError(f"Invalid JSON at line {line_no}: {e}") from e

            if not isinstance(event, dict):
                if skip_invalid:
                    continue
                raise ValueError(f"Line {line_no} is not a JSON object")

            events.append(event)

    return events


# ── RawEvent 解析 ───────────────────────────────────────────────────


def parse_raw_event(data: dict[str, Any]) -> RawEvent:
    """
    将单条 JSON 对象解析为 RawEvent。

    提取约定字段（event_id, user_id, session_id, task_id, event_type,
    scenario, timestamp），其余字段归入 payload。

    Parameters
    ----------
    data : dict[str, Any]
        从 JSONL 解析出的原始数据。

    Returns
    -------
    RawEvent
        封装后的原始事件对象。

    Raises
    ------
    ValueError
        缺少必需字段或字段类型/值非法时抛出。
    """
    # --- 必需字段检查 ---
    event_id = data.get("event_id")
    if not event_id:
        raise ValueError("Missing required field: event_id")

    user_id = data.get("user_id")
    if not user_id:
        raise ValueError("Missing required field: user_id")

    event_type_raw = data.get("event_type")
    if not event_type_raw:
        raise ValueError("Missing required field: event_type")

    # --- 字段转换与校验 ---
    try:
        event_type = EventType(event_type_raw)
    except ValueError:
        valid = [e.value for e in EventType]
        raise ValueError(
            f"Invalid event_type: '{event_type_raw}'. "
            f"Valid values: {valid}"
        )

    scenario_raw = data.get("scenario", "unknown")
    try:
        scenario = Scene(scenario_raw)
    except ValueError:
        # 对于未知场景使用 UNKNOWN 兜底，不至于直接崩溃
        scenario = Scene.UNKNOWN

    # session_id / task_id 可选，但尽量给默认值
    session_id = data.get("session_id", "")
    task_id = data.get("task_id", "")

    # timestamp 解析
    ts_raw = data.get("timestamp")
    if ts_raw:
        try:
            timestamp = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid timestamp '{ts_raw}': {e}") from e
    else:
        timestamp = datetime.now()

    # --- payload：除去约定字段的剩余部分 ---
    KNOWN_KEYS = {
        "event_id", "user_id", "session_id", "task_id",
        "event_type", "scenario", "timestamp",
    }
    payload = {k: v for k, v in data.items() if k not in KNOWN_KEYS}

    return RawEvent(
        event_id=event_id,
        user_id=user_id,
        session_id=session_id,
        task_id=task_id,
        event_type=event_type,
        scenario=scenario,
        timestamp=timestamp,
        payload=payload,
    )


# ── 高层聚合入口 ────────────────────────────────────────────────────


def collect(
    file_path: str | Path,
    skip_invalid: bool = False,
) -> CollectResult:
    """
    加载 JSONL 文件并全部解析为 RawEvent，同时收集统计信息。

    这是 ingestion 层的推荐入口函数。

    Parameters
    ----------
    file_path : str | Path
        JSONL 文件路径。
    skip_invalid : bool
        为 True 时跳过无效行继续解析；为 False（默认）时
        遇到第一个无效行即抛出异常。

    Returns
    -------
    CollectResult
        包含 RawEvent 列表、总数、成功数、跳过数、错误详情的汇总结果。
    """
    raw_dicts = load_jsonl(file_path, skip_invalid=skip_invalid)
    total = len(raw_dicts)

    events: list[RawEvent] = []
    errors: list[dict[str, Any]] = []
    skipped = 0

    for idx, data in enumerate(raw_dicts):
        try:
            event = parse_raw_event(data)
            events.append(event)
        except (ValueError, TypeError) as e:
            if skip_invalid:
                skipped += 1
                errors.append({"index": idx, "error": str(e), "data": data})
                continue
            raise

    return CollectResult(
        events=events,
        total=total,
        success=len(events),
        skipped=skipped,
        errors=errors,
    )
