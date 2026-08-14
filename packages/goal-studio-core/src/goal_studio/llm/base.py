"""LLM Provider 抽象基类"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, cast


@dataclass
class LLMMessage:
    """统一消息格式"""
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMResponse:
    """LLM 响应"""
    content: str
    """文本内容"""

    usage: dict[str, int] = field(default_factory=dict)
    """token 用量: {prompt_tokens, completion_tokens, total_tokens}"""

    model: str = ""
    """实际使用的模型"""

    finish_reason: str = "stop"
    """结束原因: stop / length / tool_calls"""

    raw: Any = None
    """原始响应(调试用)"""

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


class LLMProvider(Protocol):
    """LLM Provider 协议"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """同步补全"""
        ...

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式补全,逐 token 返回"""
        ...

    def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """结构化 JSON 输出

        内部调用 complete 后解析 JSON,失败时抛 ValueError。
        子类可覆盖以使用原生 JSON mode / tool calling。
        """
        ...


class BaseLLMProvider:
    """LLM Provider 基类(提供 complete_json 默认实现)"""

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def model(self) -> str:
        raise NotImplementedError

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        raise NotImplementedError

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""

    def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict[str, Any]:
        import json

        resp = self.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        text = resp.content.strip()

        # 尝试直接解析
        try:
            return cast(dict[str, Any], json.loads(text))
        except json.JSONDecodeError:
            pass

        # P-LOW-2-5: 用平衡括号算法提取 JSON(替代贪婪正则)
        extracted = self._extract_json(text)
        if extracted is not None:
            return extracted

        raise ValueError(
            f"LLM 响应不是有效 JSON: {text[:200]}..."
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """P-LOW-2-5: 平衡括号算法提取 JSON(专家建议,替代贪婪正则)

        正则 r"\{.*\}" 在嵌套 JSON 或多个 {} 时会贪婪匹配错误范围,
        平衡括号算法准确找到第一个完整的 {...} 或 [...] 块。

        支持:
          - ```json ... ``` 代码块
          - 纯文本中嵌入的 JSON
          - 嵌套对象/数组
        """
        import json
        import re

        # 1. 优先尝试 ```json ... ``` 代码块
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            try:
                return cast(dict[str, Any], json.loads(candidate))
            except json.JSONDecodeError:
                # 代码块内容非合法 JSON,继续用平衡括号在 candidate 中找
                extracted = BaseLLMProvider._extract_balanced(candidate)
                if extracted is not None:
                    return extracted

        # 2. 在原文中用平衡括号找第一个 {...} 或 [...]
        return BaseLLMProvider._extract_balanced(text)

    @staticmethod
    def _extract_balanced(text: str) -> dict[str, Any] | None:
        """平衡括号扫描: 找第一个完整的 {...} 或 [...]"""
        import json

        # 找第一个 { 或 [
        start = -1
        open_char = ""
        for i, ch in enumerate(text):
            if ch in "{[":
                start = i
                open_char = ch
                break
        if start == -1:
            return None

        close_char = "}" if open_char == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return cast(dict[str, Any], json.loads(candidate))
                    except json.JSONDecodeError:
                        # 这一段不是合法 JSON,从下一个 { 或 [ 继续找
                        return BaseLLMProvider._extract_balanced(text[i + 1 :])
        return None



