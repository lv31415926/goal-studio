"""server 测试全局配置

禁用 REST 速率限制: 全量回归(多测试文件连续 POST)会超过
限流中间件的 60 次/分钟阈值,触发 429 误报。限流逻辑本身
在 app 层有独立测试覆盖。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOAL_STUDIO_RATE_LIMIT", "0")
