from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


class Scene(_StrEnum):
    GLOBAL = "global"
    CHAT = "chat"
    TOOL = "tool"
    WORKFLOW = "workflow"
    DOCUMENT = "document"
    CODE = "code"
    SYSTEM = "system"
    OTHER = "other"


class EventType(_StrEnum):
    CONVERSATION = "conversation"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    USER_BEHAVIOR = "user_behavior"
    USER_CONFIG = "user_config"
    SYSTEM = "system"


class MemoryType(_StrEnum):
    PREFERENCE = "preference"
    KNOWLEDGE = "knowledge"
    WORKFLOW = "workflow"
    CASE = "case"
    TEMPLATE = "template"
    TOOL = "tool"
    ENVIRONMENT = "environment"
    SAFETY = "safety"

