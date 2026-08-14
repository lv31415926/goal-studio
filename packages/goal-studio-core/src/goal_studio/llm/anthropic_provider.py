"""Anthropic LLM Provider

使用 anthropic SDK,支持 Claude 3.5 Sonnet / Haiku。
通过 ANTHROPIC_API_KEY 环境变量认证。
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from goal_studio.llm.base import BaseLLMProvider, LLMMessage, LLMResponse


class AnthropicLLMProvider(BaseLLMProvider):
    """Anthropic Claude Provider"""

    def __init__(
        self,
        model: str = "claude-3-5-haiku-20241022",
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._client: Any = None
        self._async_client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise ImportError(
                    "anthropic 未安装,请 pip install 'goal-studio[llm]'"
                ) from e
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as e:
                raise ImportError(
                    "anthropic 未安装,请 pip install 'goal-studio[llm]'"
                ) from e
            self._async_client = AsyncAnthropic(api_key=self._api_key)
        return self._async_client

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()
        # Anthropic 分离 system 和 messages
        system_parts = [m.content for m in messages if m.role == "system"]
        user_messages = [
            {"role": m.role, "content": m.content}
            for m in messages if m.role != "system"
        ]
        resp = client.messages.create(
            model=self._model,
            system="\n\n".join(system_parts) if system_parts else None,
            messages=user_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        content = ""
        for block in resp.content:
            if hasattr(block, "text"):
                content += block.text
        # 与 OpenAI provider 一致的 usage None 保护
        usage_in = resp.usage.input_tokens if resp.usage else 0
        usage_out = resp.usage.output_tokens if resp.usage else 0
        return LLMResponse(
            content=content,
            usage={
                "prompt_tokens": usage_in,
                "completion_tokens": usage_out,
                "total_tokens": usage_in + usage_out,
            },
            model=resp.model,
            finish_reason=resp.stop_reason or "stop",
            raw=resp,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        client = self._get_async_client()
        system_parts = [m.content for m in messages if m.role == "system"]
        user_messages = [
            {"role": m.role, "content": m.content}
            for m in messages if m.role != "system"
        ]
        async with client.messages.stream(
            model=self._model,
            system="\n\n".join(system_parts) if system_parts else None,
            messages=user_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ) as stream:
            async for text in stream.text_stream:
                yield text
