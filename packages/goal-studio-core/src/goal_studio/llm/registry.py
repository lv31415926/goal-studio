"""LLM 注册中心: 按 provider 名获取 LLM 实例

环境变量:
  GOAL_STUDIO_LLM_PROVIDER=openai|anthropic|mock (默认 mock)
  GOAL_STUDIO_LLM_MODEL=模型名
  OPENAI_API_KEY / ANTHROPIC_API_KEY
"""
from __future__ import annotations

import os
from typing import Any

from goal_studio.llm.base import BaseLLMProvider, LLMProvider
from goal_studio.llm.mock_provider import MockLLMProvider

_default_llm: BaseLLMProvider | None = None


def configure_llm(
    provider: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> BaseLLMProvider:
    """配置全局 LLM

    Args:
        provider: provider 名 (openai/anthropic/mock),None 则读环境变量
        model: 模型名,None 则读环境变量
        **kwargs: 传给 provider 构造函数
    """
    global _default_llm

    provider = provider or os.getenv("GOAL_STUDIO_LLM_PROVIDER", "mock")
    model = model or os.getenv("GOAL_STUDIO_LLM_MODEL", "")

    if provider == "mock":
        _default_llm = MockLLMProvider(model=model or "mock-llm")
    elif provider == "openai":
        from goal_studio.llm.openai_provider import OpenAILLMProvider
        _default_llm = OpenAILLMProvider(
            model=model or "gpt-4o-mini",
            api_key=kwargs.get("api_key"),
            base_url=kwargs.get("base_url"),
        )
    elif provider == "anthropic":
        from goal_studio.llm.anthropic_provider import AnthropicLLMProvider
        _default_llm = AnthropicLLMProvider(
            model=model or "claude-3-5-haiku-20241022",
            api_key=kwargs.get("api_key"),
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    return _default_llm


def get_llm() -> BaseLLMProvider:
    """获取全局 LLM (首次调用时按环境变量初始化)"""
    global _default_llm
    if _default_llm is None:
        _default_llm = configure_llm()
    return _default_llm


_ = LLMProvider
