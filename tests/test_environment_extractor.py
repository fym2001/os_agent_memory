from __future__ import annotations

import json
from typing import Any

import pytest

from core.constants import MemoryType
from extractors.environment_extractor import EnvironmentExtractor
from extractors.llm_memory_extractor import clear_default_llm_client, set_default_llm_client


class EnvironmentFakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(prompt)
        self.calls.append({"request": request, "prompt": prompt, "schema": schema})
        return {
            "candidates": [
                {
                    "is_memory_worthy": True,
                    "is_long_term": True,
                    "memory_type": "environment",
                    "category": "path",
                    "value": "documents",
                    "scope": "system",
                    "content": "常用文档目录是 /root/docs",
                    "confidence": 0.9,
                    "evidence": "documents=/root/docs",
                    "reason": "LLM 从系统上下文中识别出可复用目录配置。",
                    "sensitivity": "none",
                }
            ]
        }


@pytest.fixture()
def env_client() -> EnvironmentFakeLLMClient:
    client = EnvironmentFakeLLMClient()
    set_default_llm_client(client)
    try:
        yield client
    finally:
        clear_default_llm_client()


def test_environment_extractor_wraps_tool_output_for_llm(env_client: EnvironmentFakeLLMClient) -> None:
    output = {"user_id": "u-env", "documents": "/root/docs", "api_key": "sk-secret-1234567890"}

    candidates = EnvironmentExtractor.extract_from_tool_output(output)

    assert candidates[0].memory_type is MemoryType.ENVIRONMENT
    assert candidates[0].key == "environment.path.documents"
    request = env_client.calls[0]["request"]
    assert request["mode"] == "environment_from_tool_output"
    assert request["events"][0]["output"]["documents"] == "/root/docs"
    assert request["events"][0]["output"]["api_key"] == "[REDACTED_SECRET]"
    assert "sk-secret-1234567890" not in env_client.calls[0]["prompt"]


def test_environment_extractor_has_no_rule_fallback_without_client() -> None:
    clear_default_llm_client()

    assert EnvironmentExtractor.extract_from_tool_output({"documents": "/root/docs"}) == []


def test_environment_extractor_rejects_non_dict_and_empty_input(env_client: EnvironmentFakeLLMClient) -> None:
    assert EnvironmentExtractor.extract_from_tool_output({}) == []
    assert EnvironmentExtractor.extract_from_tool_output([]) == []  # type: ignore[arg-type]
    assert env_client.calls == []
