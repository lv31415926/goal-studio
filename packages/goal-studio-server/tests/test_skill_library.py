"""Voyager 技能库闭环: validate PASS 沉淀 → draft 检索注入 few-shot"""
from __future__ import annotations

import json

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


@pytest.fixture
def client(env):
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
        yield c, spy


def test_validate_pass_stores_pattern(client):
    """PASS(≥60) 验证后, L1 骨架以 JSON 沉淀为 pattern 记忆"""
    c, spy = client
    sid = _mk_session(c, goal="创建量化交易技能")
    _draft(c, sid, goal="创建量化交易技能")
    spy.scores = [88.0]
    r = c.post(f"/api/sessions/{sid}/validate")
    assert r.status_code == 200 and r.json()["passed"] is True

    entries = get_knowledge_store().query(category="pattern")
    assert len(entries) == 1
    pat = json.loads(entries[0].value)
    assert pat["goal"] == "创建量化交易技能"
    assert pat["score"] == 88.0
    assert any(n["title"].startswith("任务") for n in pat["l1_nodes"])
    assert entries[0].metadata["session_id"] == sid
    # 置信度随分数提升: 0.5 + 88/200
    assert entries[0].confidence == pytest.approx(0.5 + 88 / 200)


def test_validate_fail_no_pattern(client):
    """FAIL(<60) 不沉淀"""
    c, spy = client
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [40.0]
    r = c.post(f"/api/sessions/{sid}/validate")
    assert r.json()["passed"] is False
    assert get_knowledge_store().query(category="pattern") == []


def test_revalidate_overwrites_not_duplicates(client):
    """同会话重复 validate → key 覆盖,不堆积"""
    c, spy = client
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [70.0]
    c.post(f"/api/sessions/{sid}/validate")
    spy.scores = [90.0]
    c.post(f"/api/sessions/{sid}/validate")
    entries = get_knowledge_store().query(category="pattern")
    assert len(entries) == 1
    assert json.loads(entries[0].value)["score"] == 90.0


def test_draft_retrieves_pattern_as_fewshot(client):
    """相似目标的新会话 draft → prior_patterns 注入 goal.metadata"""
    c, spy = client
    # 会话1: 量化交易技能 PASS 沉淀
    sid1 = _mk_session(c, goal="创建量化交易技能")
    _draft(c, sid1, goal="创建量化交易技能")
    spy.scores = [88.0]
    assert c.post(f"/api/sessions/{sid1}/validate").json()["passed"] is True

    # 会话2: 相似目标 draft → 检索注入
    sid2 = _mk_session(c, goal="创建一个量化交易回测技能")
    _draft(c, sid2, goal="创建一个量化交易回测技能")
    patterns = spy.drafted_goals[-1].metadata.get("prior_patterns")
    assert patterns, "相似目标应检索到成功结构"
    assert patterns[0]["goal"] == "创建量化交易技能"


def test_draft_unrelated_goal_no_injection(client):
    """无关目标不注入(关键词无重合)"""
    c, spy = client
    sid1 = _mk_session(c, goal="创建量化交易技能")
    _draft(c, sid1, goal="创建量化交易技能")
    spy.scores = [88.0]
    c.post(f"/api/sessions/{sid1}/validate")

    sid2 = _mk_session(c, goal="写一首关于海鸥的诗")
    _draft(c, sid2, goal="写一首关于海鸥的诗")
    assert "prior_patterns" not in spy.drafted_goals[-1].metadata


def test_corrupt_pattern_skipped(client):
    """坏 pattern 条目(非法 JSON)跳过,不阻断 draft"""
    from goal_studio.core.knowledge_store import KnowledgeEntry

    store = get_knowledge_store()
    store.put(KnowledgeEntry(
        id="bad", category="pattern", key="pattern:bad",
        value="{not-json", source="auto",
    ))
    c, spy = client
    sid = _mk_session(c, goal="创建量化交易技能")
    r = c.post(f"/api/sessions/{sid}/draft", json={
        "goal": {"goal": "创建量化交易技能", "product_type": "skill"},
    })
    assert r.status_code == 200
    assert "prior_patterns" not in spy.drafted_goals[-1].metadata
