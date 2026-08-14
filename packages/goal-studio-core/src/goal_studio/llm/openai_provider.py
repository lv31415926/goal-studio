"""OpenAI LLM Provider

使用 openai SDK,支持 GPT-4o / GPT-4o-mini 等。
通过 OPENAI_API_KEY 环境变量认证。
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator
from typing import Any, cast

from goal_studio.llm.base import BaseLLMProvider, LLMMessage, LLMResponse

_logger = logging.getLogger(__name__)


class OpenAILLMProvider(BaseLLMProvider):
    """OpenAI Provider"""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._base_url = base_url or os.getenv("OPENAI_BASE_URL")
        # P-LOW-2-5: 超时 + 重试可由环境变量配置,避免 LLM 卡死阻塞流水线
        self._timeout = float(os.getenv("OPENAI_TIMEOUT", str(timeout)))
        self._max_retries = int(os.getenv("OPENAI_MAX_RETRIES", str(max_retries)))
        self._client: Any = None
        self._async_client: Any = None
        # 双检锁: 避免多线程并发初始化 client
        self._client_lock = threading.Lock()
        self._async_client_lock = threading.Lock()

    def _get_client(self) -> Any:
        # P-LOW-2-5: 双检锁 — 避免多线程并发初始化 client
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    try:
                        from openai import OpenAI
                    except ImportError as e:
                        raise ImportError(
                            "openai 未安装,请 pip install 'goal-studio[llm]'"
                        ) from e
                    kwargs: dict[str, Any] = {
                        "api_key": self._api_key,
                        "timeout": self._timeout,
                        "max_retries": self._max_retries,
                    }
                    if self._base_url:
                        kwargs["base_url"] = self._base_url
                    self._client = OpenAI(**kwargs)
        return self._client

    def _get_async_client(self) -> Any:
        # P-LOW-2-5: 双检锁 — 避免多线程并发初始化 async client
        if self._async_client is None:
            with self._async_client_lock:
                if self._async_client is None:
                    try:
                        from openai import AsyncOpenAI
                    except ImportError as e:
                        raise ImportError(
                            "openai 未安装,请 pip install 'goal-studio[llm]'"
                        ) from e
                    kwargs: dict[str, Any] = {
                        "api_key": self._api_key,
                        "timeout": self._timeout,
                        "max_retries": self._max_retries,
                    }
                    if self._base_url:
                        kwargs["base_url"] = self._base_url
                    self._async_client = AsyncOpenAI(**kwargs)
        return self._async_client

    @property
    def name(self) -> str:
        return "openai"

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
        resp = client.chat.completions.create(
            model=self._model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = resp.choices[0]
        # 支持推理模型(如 DeepSeek-v4-flash): content 为空时回退到 reasoning_content
        content = choice.message.content or ""
        if not content:
            # 推理模型可能将实际输出放在 reasoning_content 字段
            reasoning = getattr(choice.message, "reasoning_content", None) or ""
            if reasoning:
                content = reasoning
        return LLMResponse(
            content=content,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                "total_tokens": resp.usage.total_tokens if resp.usage else 0,
            },
            model=resp.model,
            finish_reason=choice.finish_reason or "stop",
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
        stream = await client.chat.completions.create(
            model=self._model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        async with stream:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 16384,
        **kwargs: Any,
    ) -> dict[str, Any]:
        client = self._get_client()
        # 推理模型(如 DeepSeek-v4-flash)需要更大 max_tokens: 推理消耗 + 实际输出
        # 优先使用 response_format=json_object (GPT-4o / DeepSeek 支持)
        try:
            resp = client.chat.completions.create(
                model=self._model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                **kwargs,
            )
            import json
            choice = resp.choices[0]
            text = choice.message.content or ""
            finish_reason = choice.finish_reason or ""
            # 推理模型回退: content 为空时使用 reasoning_content (仅当 reasoning 内含合法 JSON)
            if not text:
                reasoning = getattr(choice.message, "reasoning_content", None) or ""
                if reasoning:
                    # reasoning_content 是推理过程而非最终输出,
                    # 仅在 finish_reason != 'length' 时尝试
                    # length 表示推理耗尽 max_tokens, content 必为空
                    if finish_reason != "length":
                        text = reasoning
                    else:
                        _logger.warning(
                            "openai_json_length_no_content",
                            extra={
                                "model": self._model,
                                "max_tokens": max_tokens,
                                "reasoning_len": len(reasoning),
                                "msg": "推理模型 max_tokens 被 reasoning_tokens 耗尽, content 为空",
                            },
                        )
            if not text:
                # 推理模型 max_tokens 不足或 LLM 返回空: 抛异常让上层 fallback
                raise ValueError(
                    f"LLM 返回空 content (finish_reason={finish_reason}, "
                    f"max_tokens={max_tokens}); 推理模型可能需要更大 max_tokens"
                )
            # 尝试直接解析
            try:
                return cast(dict[str, Any], json.loads(text))
            except json.JSONDecodeError:
                # content 可能包含 markdown 代码块或推理过程混入
                # 用 _extract_json 提取 (支持 ```json``` 包裹和平衡括号)
                try:
                    extracted = self._extract_json(text)
                    if extracted:
                        return extracted
                except Exception:
                    pass
                # 最终降级: 重新调用 LLM (不带 response_format)
                _logger.warning(
                    "openai_json_parse_failed",
                    extra={"model": self._model, "text_len": len(text), "text_head": text[:100]},
                )
                return super().complete_json(
                    messages, temperature=temperature, max_tokens=max_tokens, **kwargs
                )
        except Exception as e:
            # 网络错误/SDK 异常: 降级到基类文本解析
            _logger.warning(
                'openai_json_mode_failed',
                extra={
                    'model': self._model,
                    'msg_count': len(messages),
                    'err_type': type(e).__name__,
                },
                exc_info=True,
            )
            return super().complete_json(
                messages, temperature=temperature, max_tokens=max_tokens, **kwargs
            )
