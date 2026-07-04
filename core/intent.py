from __future__ import annotations

from dataclasses import dataclass
from typing import List

@dataclass
class IntentAnalysis:
    need_history: bool = False
    involves_preferences: bool = False
    involves_security: bool = False
    involves_forget: bool = False
    intents: List[str] | None = None
    confidence: float = 0.0

    def to_dict(self):
        return {
            "need_history": self.need_history,
            "involves_preferences": self.involves_preferences,
            "involves_security": self.involves_security,
            "involves_forget": self.involves_forget,
            "intents": self.intents or [],
            "confidence": self.confidence,
        }

# 关键词集合（第一阶段启发式规则）
_PREFER_KEYS = {"偏好", "喜欢", "不喜欢", "喜好", "prefer", "preference"}
_HISTORY_KEYS = {"历史", "记忆", "回忆", "之前", "之前说过"}
_SECURITY_KEYS = {"安全", "隐私", "策略", "policy", "权限", "授权"}
_FORGET_KEYS = {"忘记", "删除", "清除", "移除", "忘掉"}


def _text_matches_any(text: str, keywords: set) -> bool:
    if not text:
        return False
    low = text.lower()
    for kw in keywords:
        if kw in low:
            return True
    return False


def analyze_text_intent(text: str) -> IntentAnalysis:
    """
    基于单条文本（用户 query / instruction /最近一条消息）进行意图判定。
    返回 IntentAnalysis，包含四个布尔标志、检测到的 intents 列表和置信度估计（启发式）。
    """
    ia = IntentAnalysis(intents=[])
    if not text:
        return ia

    # 简单启发式匹配
    if _text_matches_any(text, _PREFER_KEYS):
        ia.involves_preferences = True
        ia.intents.append("preference")

    if _text_matches_any(text, _HISTORY_KEYS):
        ia.need_history = True
        ia.intents.append("history")

    if _text_matches_any(text, _SECURITY_KEYS):
        ia.involves_security = True
        ia.intents.append("security")

    if _text_matches_any(text, _FORGET_KEYS):
        ia.involves_forget = True
        ia.intents.append("forget")

    # 置信度：基于匹配到的意图数量的简单比例（第一阶段）
    matched = len(ia.intents)
    ia.confidence = min(1.0, 0.2 * matched) if matched > 0 else 0.0

    return ia


def analyze_events_intent(events: List[dict] | List[object]) -> IntentAnalysis:
    """
    基于一组事件（最近若干消息）进行意图判定。
    events 可以是 dict，也可以是拥有 .payload/.content 等属性（轮流尝试读取）。
    策略：合并最近 N 条文本并调用 analyze_text_intent。
    """
    if not events:
        return IntentAnalysis(intents=[])

    # 获取最近若干条文本（优先从事件的 'payload' 或 'text' 字段读取）
    texts: List[str] = []
    for ev in reversed(events[-6:]):  # 查看最近 6 条事件
        t = None
        if isinstance(ev, dict):
            # 常见字段
            t = ev.get("text") or ev.get("content") or ev.get("payload", {}).get("text")
            # 有时 payload 是字符串
            if not t and isinstance(ev.get("payload"), str):
                t = ev.get("payload")
        else:
            # 对象尝试属性访问
            t = getattr(ev, "text", None) or getattr(ev, "content", None) or None
            payload = getattr(ev, "payload", None)
            if not t and isinstance(payload, (str,)):
                t = payload
            if not t and hasattr(payload, "get"):
                t = payload.get("text")

        if t:
            texts.append(t)

    merged = " ".join(texts)
    return analyze_text_intent(merged)
