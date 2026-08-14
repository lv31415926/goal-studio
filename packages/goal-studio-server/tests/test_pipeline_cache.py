"""PipelineCache 边界测试 (P-LOW-2-10, 专家 5 建议)

P-LOW-2-1 的 PipelineCache 将在 PR-E 引入,此处先测试其 LRU+TTL 行为。
本测试在 PR-D 阶段会因 PipelineCache 尚未导入而 skip,PR-E 实施后自动启用。
"""
from __future__ import annotations

import time

import pytest

try:
    from goal_studio_server.routes.ws import PipelineCache  # PR-E 引入
    _HAS_CACHE = True
except ImportError:
    _HAS_CACHE = False

_skip_no_cache = pytest.mark.skipif(
    not _HAS_CACHE,
    reason="P-LOW-2-1 PipelineCache not yet implemented (PR-E)",
)


@_skip_no_cache
def test_pipeline_cache_lru_eviction():
    """maxsize+1 触发驱逐最旧"""
    cache = PipelineCache(maxsize=2, ttl=9999)
    cache.put("s1", object())
    cache.put("s2", object())
    cache.put("s3", object())  # 触发驱逐 s1
    assert "s1" not in cache
    assert "s2" in cache
    assert "s3" in cache


@_skip_no_cache
def test_pipeline_cache_ttl_expiry():
    """TTL 过期后 get 返回 None"""
    cache = PipelineCache(maxsize=10, ttl=0.1)
    cache.put("s1", object())
    time.sleep(0.2)
    assert cache.get("s1") is None


@_skip_no_cache
def test_pipeline_cache_touch_resets_ttl():
    """touch 更新访问时间,避免被 TTL 驱逐"""
    cache = PipelineCache(maxsize=10, ttl=0.3)
    cache.put("s1", object())
    time.sleep(0.2)
    cache.touch("s1")  # 重置 TTL
    time.sleep(0.2)  # 距 put 已 0.4s,但距 touch 仅 0.2s
    assert cache.get("s1") is not None
