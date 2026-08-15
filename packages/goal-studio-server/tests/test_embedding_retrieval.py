"""P2: server 检索升级 — hash embedding 排序 + 降级路径"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_p0_upgrade import SpyAdapter, _draft, _mk_session  # noqa: F401 (复用)

from goal_studio_server.app import create_app
from goal_studio_server.deps import (
    get_adapter_registry,
    get_knowledge_store,
    get_llm_provider,
    get_session_manager,
)
from goal_studio_server.routes.studio import _rank_entries


class _Entry:
    """最小 KnowledgeEntry 替身(仅 _rank_entries 所需字段)"""

    def __init__(self, value: str, confidence: float = 0.5, tags=None, metadata=None):
        self.value = value
        self.confidence = confidence
        self.tags = tags or []
        self.metadata = metadata or {}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOAL_STUDIO_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("GOAL_STUDIO_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GOAL_STUDIO_KNOWLEDGE_ROOT", str(tmp_path / "knowledge.json"))
    import goal_studio_server.deps as deps

    for fn in (deps.get_session_manager, deps.get_artifact_store, deps.get_knowledge_store):
        fn.cache_clear()
    yield tmp_path
    for fn in (deps.get_session_manager, deps.get_artifact_store, deps.get_knowledge_store):
        fn.cache_clear()


# ==== _rank_entries 单元 ====

def test_rank_keyword_fallback_default_off(env):
    """默认 provider=None → 关键词路径(与升级前一致)"""
    entries = [
        _Entry("量化交易回测的失败教训", 0.5),
        _Entry("写诗的失败教训", 0.5),
    ]
    ranked = _rank_entries("创建量化交易回测技能", entries)
    assert ranked and ranked[0][1].value.startswith("量化")


def test_rank_embedding_path_when_enabled(env, monkeypatch):
    """GOAL_STUDIO_EMBEDDING_PROVIDER=hash → 余弦排序(共享词多者胜)"""
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "hash")
    entries = [
        _Entry("量化 交易 回测 教训"),   # 与目标共享 量化/交易/回测
        _Entry("海鸥 诗歌 教训"),        # 无共享词
    ]
    ranked = _rank_entries("量化 交易 策略", entries)
    assert ranked, "向量路径应至少返回一个正相似条目"
    assert ranked[0][1].value.startswith("量化")


def test_rank_embedding_uses_cached_vector(env, monkeypatch):
    """metadata 缓存向量被复用(错误维度缓存触发重算不崩溃)"""
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "hash")
    from goal_studio.core.embedding import get_embedding_provider

    prov = get_embedding_provider()
    vec = prov.embed(["量化交易"])[0]
    e_cached = _Entry("量化交易", metadata={"embedding": vec})
    e_bad = _Entry("其他", metadata={"embedding": [0.1, 0.2]})  # 维度错
    ranked = _rank_entries("量化交易", [e_bad, e_cached])
    assert ranked[0][1] is e_cached


def test_rank_embedding_exception_falls_back(env, monkeypatch):
    """provider embed 抛异常 → 整体降级关键词路径"""
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "hash")

    import goal_studio.core.embedding as emb

    class _Boom:
        name = "boom"
        dim = 8

        def embed(self, texts):
            raise RuntimeError("boom")

    monkeypatch.setattr(emb, "get_embedding_provider", lambda: _Boom())
    import goal_studio_server.routes.studio as studio_mod

    monkeypatch.setattr(
        "goal_studio.core.embedding.get_embedding_provider", lambda: _Boom()
    )
    # studio._get_embed 从 embedding 模块惰性 import — patch 工厂函数
    entries = [_Entry("量化交易回测教训"), _Entry("无关内容abcdefxyz")]
    ranked = studio_mod._rank_entries("创建量化交易回测技能", entries)
    assert ranked and ranked[0][1].value.startswith("量化")


# ==== API 闭环: pattern 沉淀向量 + draft 检索 ====

def test_pattern_stores_embedding_and_draft_retrieves(env, monkeypatch):
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "hash")
    from goal_studio.core.session_manager import SessionManager

    spy = SpyAdapter()

    class _Reg:
        def get_for_goal(self, goal):
            return spy

        def get(self, t):
            return spy

    sm = SessionManager(root=env / "sessions")
    app = create_app()
    app.dependency_overrides[get_session_manager] = lambda: sm
    app.dependency_overrides[get_adapter_registry] = lambda: _Reg()
    app.dependency_overrides[get_llm_provider] = lambda: None
    with TestClient(app) as c:
        sid = _mk_session(c, goal="创建量化交易回测系统")
        _draft(c, sid, goal="创建量化交易回测系统")
        spy.scores = [88.0]
        assert c.post(f"/api/sessions/{sid}/validate").json()["passed"] is True

        entries = get_knowledge_store().query(category="pattern")
        assert len(entries) == 1
        assert "embedding" in entries[0].metadata  # 沉淀时缓存了向量

        sid2 = _mk_session(c, goal="创建量化交易回测平台")
        _draft(c, sid2, goal="创建量化交易回测平台")
        patterns = spy.drafted_goals[-1].metadata.get("prior_patterns")
        assert patterns, "向量检索应命中相似目标"
