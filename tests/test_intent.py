import pytest

from os_agent_memory.core.intent import analyze_text_intent


def test_preference_detection():
    ia = analyze_text_intent("我很喜欢辣的食物")
    assert ia.involves_preferences


def test_forget_detection():
    ia = analyze_text_intent("请帮我忘记上次谈话中提到的密码")
    assert ia.involves_forget


def test_security_detection():
    ia = analyze_text_intent("这涉及隐私和安全策略，需要注意")
    assert ia.involves_security


def test_history_detection():
    ia = analyze_text_intent("你还记得我们之前说的那个任务吗？")
    assert ia.need_history


def test_empty_text():
    ia = analyze_text_intent("")
    assert not ia.involves_preferences and not ia.involves_forget and not ia.involves_security and not ia.need_history
