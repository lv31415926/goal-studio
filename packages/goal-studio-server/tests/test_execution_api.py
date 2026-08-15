"""执行引擎 + 数据飞轮 API 测试(方案 #1/#2 服务化)

覆盖: /execute 成功与分支语义、无图 400、404、记录读取、
/refine → 飞轮样本沉淀 → /api/flywheel/stats → /train 样本不足指引。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, ProductType, Report
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer
from goal_studio.core.session_manager import Session, SessionManager, SessionPhase

from goal_studio_server.app import create_app
from goal_studio_server.deps import (
    get_adapter_registry,
    get_llm_provider,
    get_session_manager,
)


def _linear_graph() -> NodeGraph:
    g = NodeGraph(root_id="n1")
    for nid in ("n1", "n2", "n3"):
        g.add_node(Node(id=nid, layer=NodeLayer.L1_PLANNING, title=f"步骤{nid}"))
    g.add_edge(Edge(id="e1", source="n1", target="n2"))
    g.add_edge(Edge(id="e2", source="n2", target="n3"))
    return g


def _branch_graph() -> NodeGraph:
    """根节点发布 校验通过=True → true 分支执行,false 分支跳过"""
    g = NodeGraph(root_id="gate")
    g.add_node(Node(id="gate", layer=NodeLayer.L1_PLANNING, title="校验",
                    slots={"校验通过": True}))
    for nid in ("ok-path", "ng-path"):
        g.add_node(Node(id=nid, layer=NodeLayer.L1_PLANNING, title=nid))
    g.add_edge(Edge(id="c1", source="gate", target="ok-path",
                    edge_type="condition", metadata={"condition": "校验通过 == true"}))
    g.add_edge(Edge(id="c2", source="gate", target="ng-path",
                    edge_type="condition", metadata={"condition": "校验通过 == false"}))
    return g


class ExecAdapter(BaseAdapter):
    """可控适配器: draft 返回可配置图,validate 恒 80 分"""

    product_type = "skill"
    name = "exec-spy"
    description = "exec test adapter"

    def __init__(self, graph: NodeGraph | None = None) -> None:
        self._graph = graph or _linear_graph()

    def draft(self, goal: GoalContract) -> NodeGraph:
        return self._graph

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        return _linear_graph()

    def validate(self, graph: NodeGraph) -> Report:
        return Report(passed=True, score=80.0, dimensions={"quality": 80.0})

    def build(self, graph: NodeGraph) -> Artifact:
        return Artifact(product_type=ProductType.SKILL, content="x")

    def export(self, artifact: Artifact, target: str) -> str:
        return target


class _Reg:
    def __init__(self, adapter: BaseAdapter) -> None:
        self._adapter = adapter

    def get_for_goal(self, goal: GoalContract) -> BaseAdapter:  # type: ignore[no-untyped-def]
        return self._adapter


def _session(graph: NodeGraph | None, with_goal: bool = True) -> Session:
    s = Session(id=uuid4().hex[:12], name="exec-test")
    if with_goal:
        s.goal = GoalContract(goal="测试目标", success_criteria=["通过"])
    if graph is not None:
        s.graph = graph
        s.phase = SessionPhase.VISUALIZE
    return s


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """隔离环境: 会话/知识库/飞轮库全部指向 tmp,LLM 禁用"""
    monkeypatch.setenv("GOAL_STUDIO_KNOWLEDGE_ROOT", str(tmp_path / "knowledge.json"))
    monkeypatch.setenv("GOAL_STUDIO_RATCHET_DB", str(tmp_path / "ratchet.db"))
    monkeypatch.setenv("GOAL_STUDIO_FLYWHEEL_MODEL", str(tmp_path / "model.pkl"))
    deps = pytest.importorskip("goal_studio_server.deps")
    deps.get_session_manager.cache_clear()
    deps.get_knowledge_store.cache_clear()
    deps.get_ratchet_store.cache_clear()
    app = create_app()
    sm = SessionManager(root=tmp_path / "sessions")
    app.dependency_overrides[get_session_manager] = lambda: sm
    app.dependency_overrides[get_adapter_registry] = lambda: _Reg(ExecAdapter())
    app.dependency_overrides[get_llm_provider] = lambda: None
    yield TestClient(app)
    deps.get_session_manager.cache_clear()
    deps.get_knowledge_store.cache_clear()
    deps.get_ratchet_store.cache_clear()
    app.dependency_overrides.clear()


class TestExecuteEndpoint:
    def test_execute_linear_success(self, client: TestClient) -> None:
        sm = client.app.dependency_overrides[get_session_manager]()
        s = _session(_linear_graph())
        sm.save(s)
        resp = client.post(f"/api/sessions/{s.id}/execute", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["passed"] is True
        assert data["status"] == "success"
        assert data["stats"] == {"total": 3, "executed": 3, "skipped": 0,
                                 "failed": 0, "pending": 0}
        assert len(data["records"]) == 3
        assert data["phase"] == "visualize"

    def test_execute_branch_semantics(self, client: TestClient) -> None:
        sm = client.app.dependency_overrides[get_session_manager]()
        s = _session(_branch_graph())
        sm.save(s)
        resp = client.post(f"/api/sessions/{s.id}/execute", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["passed"] is True
        assert data["stats"]["executed"] == 2  # gate + ok-path
        assert data["stats"]["skipped"] == 1   # ng-path
        by_id = {r["node_id"]: r for r in data["records"]}
        assert by_id["ng-path"]["status"] == "skipped"

    def test_execute_no_graph_400(self, client: TestClient) -> None:
        sm = client.app.dependency_overrides[get_session_manager]()
        s = _session(None, with_goal=False)
        sm.save(s)
        resp = client.post(f"/api/sessions/{s.id}/execute", json={})
        assert resp.status_code == 400

    def test_execute_unknown_session_404(self, client: TestClient) -> None:
        resp = client.post("/api/sessions/000000000000/execute", json={})
        assert resp.status_code == 404

    def test_execute_records_roundtrip(self, client: TestClient) -> None:
        sm = client.app.dependency_overrides[get_session_manager]()
        s = _session(_linear_graph())
        sm.save(s)
        assert client.get(f"/api/sessions/{s.id}/execution/records").status_code == 404
        client.post(f"/api/sessions/{s.id}/execute", json={})
        resp = client.get(f"/api/sessions/{s.id}/execution/records")
        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 3
        assert records[0]["node_id"] == "n1"
        # 执行历史持久化在会话元数据中
        s2 = sm.get(s.id)
        assert s2.metadata["last_execution"]["status"] == "success"

    def test_execute_fail_nodes_injected(self, client: TestClient) -> None:
        sm = client.app.dependency_overrides[get_session_manager]()
        s = _session(_linear_graph())
        sm.save(s)
        resp = client.post(
            f"/api/sessions/{s.id}/execute", json={"fail_nodes": ["n2"]}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["passed"] is False
        assert data["status"] == "failed"
        by_id = {r["node_id"]: r for r in data["records"]}
        assert by_id["n2"]["status"] == "failed"
        assert by_id["n3"]["status"] == "skipped"


class TestFlywheel:
    def test_refine_writes_sample_and_stats(self, client: TestClient) -> None:
        sm = client.app.dependency_overrides[get_session_manager]()
        s = _session(_linear_graph())
        sm.save(s)
        # refine: old_score 0 → validate 80 → KEEP
        resp = client.post(
            f"/api/sessions/{s.id}/refine",
            json={"patch": {"op": "update_node", "target_node_id": "n1",
                            "slots": {"title": "新标题"}}},
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "keep"
        # 数据飞轮: refine 判定已沉淀为样本
        stats = client.get("/api/flywheel/stats").json()
        assert stats["total"] == 1
        assert stats["keep"] == 1 and stats["revert"] == 0
        # 样本量不足 → 训练拒绝并给出指引
        train = client.post("/api/flywheel/train").json()
        assert train["trained"] is False
        assert "样本不足" in train["error"]

    def test_predict_without_model_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/flywheel/predict",
            json={"graph_before": {"nodes": {}, "edges": []},
                  "graph_after": {"nodes": {}, "edges": []},
                  "patch": {"op": "add_node"}, "old_score": 70.0},
        )
        assert resp.status_code == 400
        assert "模型文件不存在" in resp.json()["detail"]

    def test_stats_empty(self, client: TestClient) -> None:
        stats = client.get("/api/flywheel/stats").json()
        assert stats["total"] == 0
