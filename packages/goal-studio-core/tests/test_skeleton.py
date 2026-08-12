"""骨架验证测试: 确保核心导入和基本流程可用"""
from __future__ import annotations

import pytest

from goal_studio import (
    BaseAdapter,
    GoalContract,
    Patch,
    Report,
    Artifact,
    NodeGraph,
    Node,
    Edge,
    NodeLayer,
)
from goal_studio.core.contract import ProductType, PatchOp
from goal_studio.core.node_graph import NodeStatus


def test_imports() -> None:
    """所有核心符号可导入"""
    assert BaseAdapter is not None
    assert GoalContract is not None


def test_goal_contract_validate() -> None:
    """GoalContract 校验"""
    g = GoalContract(goal="", product_type=ProductType.SKILL)
    issues = g.validate()
    assert "goal 不能为空" in issues

    g2 = GoalContract(
        goal="测试目标",
        success_criteria=["c1"],
        product_type=ProductType.SKILL,
    )
    assert g2.validate() == []


def test_node_graph_add_remove() -> None:
    """NodeGraph 增删"""
    g = NodeGraph()
    n1 = Node(id="n1", layer=NodeLayer.L1_PLANNING, title="N1")
    n2 = Node(id="n2", layer=NodeLayer.L1_PLANNING, title="N2")
    g.add_node(n1)
    g.add_node(n2)
    assert len(g.nodes) == 2

    g.add_edge(Edge(id="e1", source="n1", target="n2"))
    assert len(g.edges) == 1
    assert g.topological_order() == ["n1", "n2"]


def test_node_graph_cycle_detection() -> None:
    """拓扑校验检测环"""
    g = NodeGraph()
    g.add_node(Node(id="a", layer=NodeLayer.L1_PLANNING, title="A"))
    g.add_node(Node(id="b", layer=NodeLayer.L1_PLANNING, title="B"))
    g.add_edge(Edge(id="e1", source="a", target="b"))
    with pytest.raises(ValueError, match="cycle"):
        g.add_edge(Edge(id="e2", source="b", target="a"))


def test_node_graph_serialize() -> None:
    """序列化/反序列化"""
    g = NodeGraph()
    g.add_node(Node(id="n1", layer=NodeLayer.L1_PLANNING, title="N1", description="d"))
    d = g.to_dict()
    g2 = NodeGraph.from_dict(d)
    assert g2.nodes["n1"].title == "N1"


def test_skill_adapter_draft() -> None:
    """SkillAdapter.draft 生成 DAG"""
    from goal_studio.adapters.skill_adapter import SkillAdapter

    adapter = SkillAdapter()
    goal = GoalContract(
        goal="创建日报技能",
        success_criteria=["c1"],
        product_type=ProductType.SKILL,
    )
    graph = adapter.draft(goal)
    assert len(graph.nodes) >= 3
    assert graph.root_id is not None


def test_skill_adapter_validate_build() -> None:
    """SkillAdapter.validate + build"""
    from goal_studio.adapters.skill_adapter import SkillAdapter

    adapter = SkillAdapter()
    goal = GoalContract(goal="g", product_type=ProductType.SKILL)
    graph = adapter.draft(goal)
    report = adapter.validate(graph)
    assert 0 <= report.score <= 100

    artifact = adapter.build(graph)
    assert artifact.product_type == ProductType.SKILL
    assert "SKILL.md" in artifact.files


def test_ratchet_gate_keep_revert() -> None:
    """棘轮门控 KEEP/REVERT"""
    from goal_studio.core.ratchet_gate import RatchetGate, RatchetDecision

    gate = RatchetGate()
    # KEEP: new > old
    d1 = gate.judge(old_score=70, new_score=80, current_graph=NodeGraph())
    assert d1 == RatchetDecision.KEEP
    assert gate.best_score == 80

    # REVERT: new <= old
    d2 = gate.judge(old_score=80, new_score=75, current_graph=NodeGraph())
    assert d2 == RatchetDecision.REVERT


def test_session_manager() -> None:
    """SessionManager 创建/加载"""
    import tempfile

    from goal_studio.core.session_manager import SessionManager, SessionPhase

    with tempfile.TemporaryDirectory() as tmp:
        sm = SessionManager(tmp)
        s = sm.create(name="test", goal=GoalContract(goal="g", product_type=ProductType.SKILL))
        assert s.phase == SessionPhase.INIT

        # 重新加载
        sm._cache.clear()
        s2 = sm.get(s.id)
        assert s2.name == "test"


def test_artifact_store() -> None:
    """ArtifactStore 保存/加载"""
    import tempfile

    from goal_studio.core.artifact_store import LocalFileArtifactStore

    with tempfile.TemporaryDirectory() as tmp:
        store = LocalFileArtifactStore(tmp)
        artifact = Artifact(
            product_type=ProductType.SKILL,
            content="test content",
            files={"SKILL.md": "test"},
            version="0.1.0",
        )
        aid = store.save(artifact, note="test")
        assert aid.startswith("skill-")

        loaded = store.load(aid)
        assert loaded.content == "test content"
        assert len(store.list_versions(aid)) == 1


def test_adapter_registry() -> None:
    """适配器注册中心"""
    from goal_studio.core.adapter_registry import AdapterRegistry, get_default_registry

    registry = get_default_registry()
    adapters = registry.list_adapters()
    assert any(a["product_type"] == "skill" for a in adapters)

    adapter = registry.get("skill")
    assert adapter.product_type == "skill"
