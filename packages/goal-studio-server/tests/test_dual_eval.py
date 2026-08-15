"""P1: /refine 双评估器接入 + LLM 反思升级

FakeJudgeLLM 按 system prompt 关键字路由(与 MockLLMProvider 同机制):
独立评审官 → judge 分;仲裁评审官 → tie 分;复盘 → lesson。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from goal_studio.llm.base import BaseLLMProvider, LLMMessage
from test_p0_upgrade import SpyAdapter, _draft, _mk_session  # noqa: F401 (复用)

from goal_studio_server.app import create_app
from goal_studio_server.deps import (
    get_adapter_registry,
    get_knowledge_store,
    get_llm_provider,
    get_session_manager,
)


class FakeJudgeLLM(BaseLLMProvider):
    """评审/复盘 fake — 记录 persona 调用序列,分数可配置"""

    def __init__(
        self,
        judge_score: float = 85.0,
        tie_score: float = 92.0,
        lesson: str = "LLM教训:精化前应逐项核对评估问题,补充关键节点参数后再提交。",
        reflection_payload: dict[str, Any] | None = None,
    ) -> None:
        self.judge_score = judge_score
        self.tie_score = tie_score
        self.lesson = lesson
        self.reflection_payload = reflection_payload  # None → 正常 lesson
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "fake-judge"

    @property
    def model(self) -> str:
        return "fake-judge"

    def complete(self, messages: list[LLMMessage], **kwargs: Any) -> Any:
        raise NotImplementedError

    def complete_json(self, messages: list[LLMMessage], **kwargs: Any) -> dict[str, Any]:
        system = messages[0].content if messages else ""
        if "仲裁评审官" in system:
            self.calls.append("tiebreaker")
            return {"score": self.tie_score, "dimensions": {}, "issues": [], "suggestions": []}
        if "独立评审官" in system:
            self.calls.append("judge")
            return {"score": self.judge_score, "dimensions": {}, "issues": [], "suggestions": []}
        if "复盘" in system:
            self.calls.append("reflection")
            if self.reflection_payload is not None:
                return self.reflection_payload
            return {"lesson": self.lesson}
        return {"score": 50.0}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOAL_STUDIO_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("GOAL_STUDIO_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GOAL_STUDIO_KNOWLEDGE_ROOT", str(tmp_path / "knowledge.json"))
    monkeypatch.delenv("GOAL_STUDIO_DUAL_EVAL", raising=False)
    import goal_studio_server.deps as deps

    for fn in (deps.get_session_manager, deps.get_artifact_store, deps.get_knowledge_store):
        fn.cache_clear()
    yield tmp_path
    for fn in (deps.get_session_manager, deps.get_artifact_store, deps.get_knowledge_store):
        fn.cache_clear()


def _client(env, llm: BaseLLMProvider | None):
    from goal_studio.core.session_manager import SessionManager

    spy = SpyAdapter()

    class _Reg:
        def get_for_goal(self, goal):
            return spy

        def get(self, t):
            return spy

    app = create_app()
    sm = SessionManager(root=env / "sessions")
    app.dependency_overrides[get_session_manager] = lambda: sm
    app.dependency_overrides[get_adapter_registry] = lambda: _Reg()
    app.dependency_overrides[get_llm_provider] = lambda: llm
    return TestClient(app), spy


def _refine(c: TestClient, sid: str) -> Any:
    r = c.post(f"/api/sessions/{sid}/refine", json={
        "patch": {"op": "update_node", "target_node_id": "l1-a",
                  "slots": {"description": "改"}, "source": "text", "rationale": "t"},
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_dual_eval_agreement_averages(env):
    """规则 95 + LLM 88(分差 7 ≤15) → 均分 91.5 进棘轮,LLM 评审被调用"""
    fake = FakeJudgeLLM(judge_score=88.0)
    c, spy = _client(env, fake)
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 95.0]
    assert c.post(f"/api/sessions/{sid}/validate").status_code == 200

    body = _refine(c, sid)
    assert body["decision"] == "keep"
    assert body["new_score"] == pytest.approx(91.5)
    assert "judge" in fake.calls
    assert "tiebreaker" not in fake.calls  # 无冲突不仲裁


def test_dual_eval_conflict_arbitrates_median(env):
    """规则 95 + LLM 70(分差 25 >15) → tiebreaker 92 → median=92 进棘轮"""
    fake = FakeJudgeLLM(judge_score=70.0, tie_score=92.0)
    c, spy = _client(env, fake)
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 95.0]
    assert c.post(f"/api/sessions/{sid}/validate").status_code == 200

    body = _refine(c, sid)
    assert "judge" in fake.calls
    assert "tiebreaker" in fake.calls  # 冲突触发仲裁
    assert body["decision"] == "keep"  # median(95,70,92)=92 ≥ 90
    assert body["new_score"] == pytest.approx(92.0)


def test_dual_eval_disabled_by_env(env, monkeypatch):
    """GOAL_STUDIO_DUAL_EVAL=0 → 不调用 LLM 评审,按规则分判定"""
    monkeypatch.setenv("GOAL_STUDIO_DUAL_EVAL", "0")
    fake = FakeJudgeLLM(judge_score=88.0)
    c, spy = _client(env, fake)
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 95.0]
    assert c.post(f"/api/sessions/{sid}/validate").status_code == 200

    body = _refine(c, sid)
    assert fake.calls == []
    assert body["new_score"] == pytest.approx(95.0)


def test_llm_none_falls_back_single(env):
    """LLM 不可用 → 单评估器(既有路径回归)"""
    c, spy = _client(env, None)
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 95.0]
    assert c.post(f"/api/sessions/{sid}/validate").status_code == 200
    body = _refine(c, sid)
    assert body["decision"] == "keep"
    assert body["new_score"] == pytest.approx(95.0)


def test_reflection_uses_llm_lesson(env):
    """REVERT 时反思用 LLM 教训(规则 50 + LLM 52 均分 51 → 回滚)"""
    fake = FakeJudgeLLM(judge_score=52.0)
    c, spy = _client(env, fake)
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 50.0]
    assert c.post(f"/api/sessions/{sid}/validate").status_code == 200

    body = _refine(c, sid)
    assert body["decision"] == "revert"
    assert "reflection" in fake.calls
    entries = get_knowledge_store().query(category="experience")
    assert len(entries) == 1
    assert fake.lesson in entries[0].value  # LLM 教训原文入库


def test_reflection_falls_back_to_template(env):
    """LLM 教训字段缺失 → 降级确定性模板(含判定词)"""
    fake = FakeJudgeLLM(judge_score=52.0, reflection_payload={"nope": True})
    c, spy = _client(env, fake)
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 50.0]
    assert c.post(f"/api/sessions/{sid}/validate").status_code == 200

    body = _refine(c, sid)
    assert body["decision"] == "revert"
    entries = get_knowledge_store().query(category="experience")
    assert len(entries) == 1
    assert "revert" in entries[0].value  # 模板内容
