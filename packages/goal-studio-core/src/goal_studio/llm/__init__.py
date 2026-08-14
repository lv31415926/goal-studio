"""goal-studio LLM 抽象层

支持多 provider (OpenAI / Anthropic),统一接口:
  - complete(messages, **kwargs) -> str      同步补全
  - stream(messages, **kwargs) -> AsyncIter  流式补全
  - complete_json(messages, schema, **kwargs) -> dict  结构化输出

通过环境变量配置:
  OPENAI_API_KEY / ANTHROPIC_API_KEY
  GOAL_STUDIO_LLM_PROVIDER=openai|anthropic|mock (默认 mock)
  GOAL_STUDIO_LLM_MODEL=gpt-4o-mini|claude-haiku|...
"""

from goal_studio.llm.base import LLMProvider, LLMMessage, LLMResponse
from goal_studio.llm.mock_provider import MockLLMProvider
from goal_studio.llm.registry import get_llm, configure_llm

__all__ = [
    "LLMProvider", "LLMMessage", "LLMResponse",
    "MockLLMProvider",
    "get_llm", "configure_llm",
]
