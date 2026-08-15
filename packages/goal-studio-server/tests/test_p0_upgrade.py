"""P0 升级回归: G1 经验闭环 / G3 撤销 / G4 导出安装 / G5 DONE

用 SpyAdapter 控制评分序列,确定性驱动 REVERT/KEEP/快照。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, ProductType, Report
from goal_studio.core.node_graph import Node, NodeGraph, NodeLayer

from goal_studio_server.app import create_app
from goal_studio_server.deps import (
    get_adapter_registry,
    get_knowledge_store,
    get_llm_provider,
    get_session_manager,
)


def _graph(tag: str = "v1") -> NodeGraph:
    g = NodeGraph(root_id="l1-a")
    g.add_node(Node(id="l1-a", layer=NodeLayer.L1_PLANNING, title=f"任务{tag}",
                    description="spy 节点"))
    return g


class SpyAdapter(BaseAdapter):
    """可控适配器: 记录 draft 收到的 goal,评分由队列驱动"""

    product_type = "skill"
    name = "spy"
    description = "spy adapter for tests"

    def __init__(self) -> None:
        self.drafted_goals: list[GoalContract] = []
        self.scores: list[float] = []
        self._i = 0

    def _next_score(self) -> float:
        if self.scores:
            return self.scores[min(self._i, len(self.scores) - 1)]
        return 80.0

    def draft(self, goal: GoalContract) -> NodeGraph:
        self.drafted_goals.append(goal)
        self._i = 0
        return _graph("draft")

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        self._i += 1
        return _graph(f"refine-{self._i}")

    def validate(self, graph: NodeGraph) -> Report:
        score = self._next_score()
        self._i += 1
        return Report(
            passed=score >= 60, score=score,
            dimensions={"quality": score},
            issues=[] if score >= 60 else ["分数过低"],
            suggestions=[], evaluator="spy",
        )

    def build(self, graph: NodeGraph) -> Artifact:
        return Artifact(
            product_type=ProductType.SKILL,
            content="spy content",
            files={"SKILL.md": "# spy skill"},
            metadata={"skill_name": "spy-skill"},
        )

    def export(self, artifact: Artifact, target: str) -> str:
        from urllib.parse import unquote, urlparse

        path = Path(unquote(urlparse(target).path))
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(artifact.files["SKILL.md"], encoding="utf-8")
        return str(path)

    def install(self, artifact: Artifact, env: dict[str, str]) -> str:
        d = Path(env.get("skill_dir", "./output"))
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(artifact.files["SKILL.md"], encoding="utf-8")
        return f"installed to {d}"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离: 会话/产物/知识库三个根目录全部指向 tmp_path"""
    monkeypatch.setenv("GOAL_STUDIO_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("GOAL_STUDIO_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GOAL_STUDIO_KNOWLEDGE_ROOT", str(tmp_path / "knowledge.json"))
    import goal_studio_server.deps as deps

    deps.get_session_manager.cache_clear()
    deps.get_artifact_store.cache_clear()
    deps.get_knowledge_store.cache_clear()
    yield tmp_path
    deps.get_session_manager.cache_clear()
    deps.get_artifact_store.cache_clear()
    deps.get_knowledge_store.cache_clear()


@pytest.fixture
def client(env, monkeypatch):
    from goal_studio.core.session_manager import SessionManager

    spy = SpyAdapter()

    class _Reg:
        def get_for_goal(self, goal: GoalContract) -> BaseAdapter:
            return spy

        def get(self, t: str) -> BaseAdapter:
            return spy

    # 单例 SessionManager(与生产 lru_cache 语义一致) —
    # 每请求新建会导致跨请求内存态(ratchet.snapshots)丢失,undo 不可测
    sm = SessionManager(root=env / "sessions")
    app = create_app()
    app.dependency_overrides[get_session_manager] = lambda: sm
    app.dependency_overrides[get_adapter_registry] = lambda: _Reg()
    app.dependency_overrides[get_llm_provider] = lambda: None
    with TestClient(app) as c:
        yield c, spy


def _mk_session(client: TestClient, goal: str = "创建量化交易技能") -> str:
    r = client.post("/api/sessions", json={
        "name": "p0", "goal": {"goal": goal, "product_type": "skill"},
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _draft(client: TestClient, sid: str, goal: str = "创建量化交易技能") -> Any:
    r = client.post(f"/api/sessions/{sid}/draft", json={
        "goal": {"goal": goal, "product_type": "skill"},
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_g1_draft_injects_prior_lessons(client):
    """知识库存有相关经验时,draft 注入 prior_lessons 到 goal.metadata"""
    c, spy = client
    store = get_knowledge_store()
    from goal_studio.core.knowledge_store import KnowledgeEntry

    store.put(KnowledgeEntry(
        id="e1", category="experience", key="reflection:t1:1",
        value="量化交易技能若缺少回测验证节点,评估分数会下降并被回滚",
        source="auto", confidence=0.6, tags=["量化", "交易", "回测"],
    ))
    sid = _mk_session(c)
    _draft(c, sid, goal="创建一个量化交易技能")
    assert spy.drafted_goals, "draft 应被调用"
    lessons = spy.drafted_goals[-1].metadata.get("prior_lessons")
    assert lessons, "相关经验应被注入"
    assert "回测" in lessons[0]


def test_g1_revert_writes_reflection(client):
    """REVERT 决策触发反思写入 KnowledgeStore"""
    c, spy = client
    sid = _mk_session(c)
    _draft(c, sid)
    # validate: 90 分(高基线) → refine validate: 50 分 → REVERT
    spy.scores = [90.0, 50.0]
    r = c.post(f"/api/sessions/{sid}/validate")
    assert r.status_code == 200
    r = c.post(f"/api/sessions/{sid}/refine", json={
        "patch": {"op": "update_node", "target_node_id": "l1-a",
                  "slots": {"description": "改"}, "source": "text", "rationale": "t"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "revert"

    entries = get_knowledge_store().query(category="experience")
    assert len(entries) == 1
    assert "revert" in entries[0].value
    assert entries[0].metadata.get("session_id") == sid


def test_g3_undo_endpoint_shape(client):
    """undo 返回 graph + snapshots_remaining + phase"""
    c, spy = client
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 95.0]
    c.post(f"/api/sessions/{sid}/validate")
    r = c.post(f"/api/sessions/{sid}/refine", json={
        "patch": {"op": "update_node", "target_node_id": "l1-a",
                  "slots": {"description": "x"}, "source": "text", "rationale": "t"},
    })
    assert r.json()["decision"] == "keep"

    r = c.post(f"/api/sessions/{sid}/ratchet/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["graph"]["nodes"], "撤销响应应含图"
    assert body["snapshots_remaining"] == 0
    assert body["phase"] == "refine"
    # 再撤销 → 400
    assert c.post(f"/api/sessions/{sid}/ratchet/undo").status_code == 400


def test_g4_g5_export_install_and_done(client, env):
    """build → export(默认路径) → install(file) → phase=done"""
    c, spy = client
    sid = _mk_session(c)
    _draft(c, sid)
    spy.scores = [90.0, 90.0]
    assert c.post(f"/api/sessions/{sid}/validate").status_code == 200
    assert c.post(f"/api/sessions/{sid}/build").status_code == 200

    # export 默认路径: 产物库同级 goal-studio-exports/{sid}
    r = c.post(f"/api/sessions/{sid}/export", json={"target": ""})
    assert r.status_code == 200, r.text
    location = r.json()["location"]
    assert str(env / "goal-studio-exports" / sid) in location
    assert (env / "goal-studio-exports" / sid / "SKILL.md").exists()
    assert r.json()["phase"] == "done"

    # install file 目标
    install_dir = env / "installed"
    r = c.post(f"/api/sessions/{sid}/install", json={
        "target": "file", "skill_dir": str(install_dir),
    })
    assert r.status_code == 200, r.text
    assert (install_dir / "SKILL.md").exists()
    assert r.json()["phase"] == "done"

    # 无产物会话 export → 400
    sid2 = _mk_session(c, "另一个目标")
    assert c.post(f"/api/sessions/{sid2}/export", json={"target": ""}).status_code == 400
