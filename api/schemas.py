"""
API 请求/响应数据模型

该文件只定义 HTTP API 层的请求与响应结构。
不要在这里写业务逻辑。

职责：
- 校验外部请求
- 约束字段类型
- 定义统一响应格式
- 与 core/models.py 通过 api/mappers.py 转换

对应接口：
- POST /memory/events
- POST /memory/extract
- POST /memory/retrieve
- POST /memory/forget
- GET  /memory/health
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from os_agent_memory.core.constants import (
    ActorType,
    EventSource,
    EventType,
    ForgetMode,
    MemoryStatus,
    MemoryType,
    RetrievalMode,
    Scene,
)


# =========================
# 通用响应
# =========================

class APIResponse(BaseModel):
    """
    统一 API 响应。

    code = 0 表示成功。
    非 0 表示失败。
    """

    code: int = 0
    message: str = "success"
    data: Any | None = None
    request_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


# =========================
# POST /memory/events
# =========================

class PostEventsRequest(BaseModel):
    """
    POST /memory/events 请求。

    作用：
    OS Agent 或 Mock Agent 将原始事件写入 Memory 系统。

    注意：
    这里接收的是 Agent Raw Payload。
    后续由 api/mappers.py 封装成 RawEvent，
    再由 ingestion/adapter.py 转成 MemoryEvent。
    """

    event_type: EventType
    user_id: str
    session_id: str
    task_id: str

    scenario: Scene = Scene.GLOBAL

    # 事件来源通道，例如 os_agent / mock_agent / tool_runtime
    source: EventSource = EventSource.OS_AGENT

    # 事件主体，例如 user / agent / tool / system
    actor: ActorType | None = None

    # Agent 原始 payload，保留原始结构
    payload: dict[str, Any]

    # 外部系统传入的时间，可选；不传则由服务端生成
    timestamp: datetime | None = None


class EventResponse(BaseModel):
    """
    事件保存响应。
    """

    raw_event_id: str
    memory_event_id: str
    user_id: str
    session_id: str
    task_id: str
    event_type: EventType
    status: str = "saved"


# =========================
# POST /memory/extract
# =========================

class PostExtractRequest(BaseModel):
    """
    POST /memory/extract 请求。

    作用：
    从某个 session/task 下的事件中抽取候选记忆，
    并可选择是否直接提交为正式记忆。
    """

    user_id: str

    # 可以按 session 抽取，也可以按 task 抽取
    session_id: str | None = None
    task_id: str | None = None

    # 指定抽取哪些记忆类型；为空表示自动抽取全部支持类型
    memory_types: list[MemoryType] = Field(default_factory=list)

    # 是否将候选记忆直接保存为 MemoryRecord
    commit: bool = True


class CandidateItem(BaseModel):
    """
    抽取返回的候选记忆。
    """

    candidate_id: str
    memory_type: MemoryType
    key: str
    content: str
    scenario: Scene
    confidence: float
    source_events: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ExtractionResponse(BaseModel):
    """
    抽取响应。
    """

    user_id: str
    session_id: str | None = None
    task_id: str | None = None

    candidates_count: int
    saved_count: int

    candidates: list[CandidateItem] = Field(default_factory=list)
    saved_memory_ids: list[str] = Field(default_factory=list)

    # intent analysis will be populated by routes to help agent decision making
    intent_analysis: "IntentAnalysis" | None = None


# =========================
# POST /memory/retrieve
# =========================

class PostRetrieveRequest(BaseModel):
    """
    POST /memory/retrieve 请求。

    作用：
    Agent 根据当前任务 query 检索相关长期记忆。
    """

    user_id: str
    query: str

    scenario: Scene = Scene.GLOBAL

    # 需要检索的记忆类型；为空表示不过滤
    memory_types: list[MemoryType] = Field(default_factory=list)

    # 检索模式：keyword / vector / hybrid / exact_key / filter
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID

    # 返回条数
    top_k: int = Field(default=5, ge=1, le=50)

    # 默认不返回 deleted / expired / rejected 等无效记忆
    include_statuses: list[MemoryStatus] = Field(
        default_factory=lambda: [MemoryStatus.ACTIVE]
    )

    # 是否返回调试信息，例如命中原因、分数构成
    debug: bool = False


class MemoryItem(BaseModel):
    """
    检索返回的单条记忆。
    """

    memory_id: str
    memory_type: MemoryType
    key: str
    content: str
    score: float
    scenario: Scene

    confidence: float | None = None
    tags: list[str] = Field(default_factory=list)
    reason: str | None = None


class RetrievalResponse(BaseModel):
    """
    检索响应。
    """

    user_id: str
    query: str
    scenario: Scene

    results: list[MemoryItem] = Field(default_factory=list)

    top_k: int
    retrieval_mode: RetrievalMode
    latency_ms: float | None = None

    intent_analysis: "IntentAnalysis" | None = None


# =========================
# POST /memory/forget
# =========================

class PostForgetRequest(BaseModel):
    """
    POST /memory/forget 请求。

    作用：
    根据用户自然语言指令执行精准遗忘。
    """

    user_id: str
    instruction: str

    scenario: Scene = Scene.GLOBAL

    # 限制遗忘的记忆类型；为空表示不限制
    memory_types: list[MemoryType] = Field(default_factory=list)

    # 遗忘模式，第一阶段默认 soft_delete
    forget_mode: ForgetMode = ForgetMode.SOFT_DELETE

    # 是否只预览将被删除的记忆，不真正执行删除
    dry_run: bool = False


class ForgetResponse(BaseModel):
    """
    遗忘响应。
    """

    user_id: str
    instruction: str
    scenario: Scene

    dry_run: bool = False

    matched_count: int
    deleted_count: int

    matched_memory_ids: list[str] = Field(default_factory=list)
    deleted_memory_ids: list[str] = Field(default_factory=list)

    log_id: str | None = None

    intent_analysis: "IntentAnalysis" | None = None


# =========================
# GET /memory/health
# =========================

class HealthResponse(BaseModel):
    """
    健康检查响应。
    """

    service: str = "os_agent_memory"
    status: str = "ok"
    timestamp: datetime = Field(default_factory=datetime.now)


# IntentAnalysis Pydantic model (placed at bottom to avoid forward ref issues)
class IntentAnalysis(BaseModel):
    need_history: bool = False
    involves_preferences: bool = False
    involves_security: bool = False
    involves_forget: bool = False
    intents: list[str] = Field(default_factory=list)
    confidence: float = 0.0
