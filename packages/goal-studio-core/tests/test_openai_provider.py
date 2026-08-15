"""C5: OpenAI provider complete_json 空 content 不再触发第二次 LLM 调用"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from goal_studio.llm.base import LLMMessage
from goal_studio.llm.openai_provider import EmptyLLMContentError, OpenAILLMProvider


def _fake_client(script: list) -> tuple[SimpleNamespace, list]:
    """script: 每次 create 调用的剧本,(content, finish_reason) 或 Exception"""
    calls: list = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            item = script[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            content, finish = item
            msg = SimpleNamespace(content=content, reasoning_content="")
            choice = SimpleNamespace(message=msg, finish_reason=finish)
            return SimpleNamespace(choices=[choice], usage=None, model="fake")

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions())), calls


def _provider(monkeypatch, client) -> OpenAILLMProvider:
    p = OpenAILLMProvider(model="fake", api_key="x")
    monkeypatch.setattr(p, "_client", client)  # 跳过惰性初始化
    return p


_MESSAGES = [LLMMessage(role="user", content="hi")]


def test_empty_content_single_call(monkeypatch) -> None:
    """C5 核心: 空 content 直接抛出,只调用 1 次 LLM"""
    client, calls = _fake_client([("", "length")])
    p = _provider(monkeypatch, client)
    with pytest.raises(EmptyLLMContentError):
        p.complete_json(_MESSAGES)
    assert len(calls) == 1, "空 content 不得触发第二次 LLM 调用"


def test_invalid_json_falls_back_once(monkeypatch) -> None:
    """C5: 有 content 但非法 JSON → 保留原有降级路径(第二次调用)"""
    client, calls = _fake_client([("not-json", "stop"), ('{"ok": true}', "stop")])
    p = _provider(monkeypatch, client)
    assert p.complete_json(_MESSAGES) == {"ok": True}
    assert len(calls) == 2


def test_network_error_falls_back(monkeypatch) -> None:
    """C5: 网络/SDK 错误仍走降级(瞬时错误重试有意义)"""
    client, calls = _fake_client([RuntimeError("conn reset"), ('{"ok": 1}', "stop")])
    p = _provider(monkeypatch, client)
    assert p.complete_json(_MESSAGES) == {"ok": 1}
    assert len(calls) == 2
