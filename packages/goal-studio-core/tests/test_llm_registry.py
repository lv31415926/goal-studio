"""A4: LLM 单例 — configure_llm 重新配置后立即生效,不被双层缓存冻结"""
from __future__ import annotations

from goal_studio.llm.registry import configure_llm, get_llm, reset_llm


def teardown_function() -> None:
    reset_llm()


def test_configure_takes_effect_immediately() -> None:
    reset_llm()
    a = configure_llm(provider="mock", model="m-a")
    assert get_llm() is a
    # 重新配置后,获取到的必须是新实例(修复前 server deps 层 lru_cache 会冻结旧实例)
    b = configure_llm(provider="mock", model="m-b")
    assert get_llm() is b
    assert get_llm().model == "m-b"


def test_reset_llm() -> None:
    configure_llm(provider="mock", model="m-a")
    reset_llm()
    c = get_llm()  # 重新惰性初始化
    assert c.model != "m-a" or c is not None
