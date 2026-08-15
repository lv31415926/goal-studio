"""P2: embedding 检索升级 + 条件边语义"""
from __future__ import annotations

import json

import pytest

from goal_studio.adapters.workflow_adapter import WorkflowAdapter
from goal_studio.core.contract import GoalContract, ProductType
from goal_studio.core.embedding import (
    HashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
)
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer

# ==== embedding ====

def test_hash_embedding_deterministic_and_normalized() -> None:
    p = HashEmbeddingProvider(dim=64)
    a1, a2 = p.embed(["量化交易回测技能"] * 2)
    assert a1 == a2  # 确定性
    assert pytest.approx(sum(v * v for v in a1), abs=1e-9) == 1.0  # L2 归一化
    assert len(a1) == 64


def test_cosine_similarity_ranking() -> None:
    p = HashEmbeddingProvider()
    goal = p.embed(["量化 交易 回测"])[0]
    near = p.embed(["量化 交易 策略"])[0]
    far = p.embed(["一首 关于 海鸥 的 诗"])[0]
    assert cosine_similarity(goal, near) > cosine_similarity(goal, far)
    assert cosine_similarity(goal, goal) > 0.999999
    # 维度不匹配 / 空向量
    assert cosine_similarity(goal, []) == -1.0
    assert cosine_similarity([0.0] * 64, [0.0] * 64) == 0.0


def test_provider_factory_default_off() -> None:
    assert get_embedding_provider() is None  # 默认 none → 关键词检索


@pytest.mark.parametrize("val", ["", "none", "off", "0"])
def test_provider_factory_off_variants(monkeypatch, val: str) -> None:
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", val)
    assert get_embedding_provider() is None


def test_provider_factory_hash_and_openai(monkeypatch) -> None:
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "hash")
    assert isinstance(get_embedding_provider(), HashEmbeddingProvider)
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_MODEL", "text-embedding-3-small")
    prov = get_embedding_provider()
    assert isinstance(prov, OpenAIEmbeddingProvider) and prov.dim == 1536


def test_provider_factory_unknown_raises(monkeypatch) -> None:
    monkeypatch.setenv("GOAL_STUDIO_EMBEDDING_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        get_embedding_provider()


# ==== 条件边: parse ====

def _goal() -> GoalContract:
    return GoalContract(goal="测试工作流", constraints=[], success_criteria=[],
                        product_type=ProductType.WORKFLOW, metadata={})


def test_parse_condition_edge_with_condition() -> None:
    adapter = WorkflowAdapter(llm=_StaticLLM({"nodes": [
        {"id": "l1-a", "layer": 1, "title": "A", "parent_id": None},
        {"id": "l1-b", "layer": 1, "title": "B", "parent_id": None},
    ], "edges": [
        {"source": "l1-a", "target": "l1-b", "edge_type": "condition",
         "condition": "分数 >= 70"},
    ], "root_id": "l1-a"}))
    g = adapter.draft(_goal())
    e = next(iter(g.edges))
    assert e.edge_type == "condition"
    assert e.metadata["condition"] == "分数 >= 70"


def test_parse_condition_edge_missing_condition_degrades() -> None:
    """condition 边缺条件文本 → 降级 data 边(不丢弃,保持连通)"""
    adapter = WorkflowAdapter(llm=_StaticLLM({"nodes": [
        {"id": "l1-a", "layer": 1, "title": "A", "parent_id": None},
        {"id": "l1-b", "layer": 1, "title": "B", "parent_id": None},
    ], "edges": [
        {"source": "l1-a", "target": "l1-b", "edge_type": "condition"},
    ], "root_id": "l1-a"}))
    g = adapter.draft(_goal())
    e = next(iter(g.edges))
    assert e.edge_type == "data"
    assert "condition" not in e.metadata


# ==== 条件边: fallback build 编译分支 ====

def _branch_graph() -> NodeGraph:
    g = NodeGraph(root_id="l1-check")
    for nid in ("l1-check", "l1-pass", "l1-fail", "l1-end"):
        g.add_node(Node(id=nid, layer=NodeLayer.L1_PLANNING, title=nid, description=f"{nid} desc"))
    g.add_edge(Edge(id="e1", source="l1-check", target="l1-pass", edge_type="condition",
                    metadata={"condition": "score >= 70"}))
    g.add_edge(Edge(id="e2", source="l1-check", target="l1-fail", edge_type="condition",
                    metadata={"condition": "score < 70"}))
    g.add_edge(Edge(id="e3", source="l1-pass", target="l1-end"))
    g.add_edge(Edge(id="e4", source="l1-fail", target="l1-end"))
    return g


def test_fallback_build_compiles_branches() -> None:
    """多出边/条件边 → next_branches 数组(when 条件 + default 兜底)"""
    adapter = WorkflowAdapter()
    files = adapter._fallback_build(_branch_graph())
    wf = json.loads(files["workflow.json"])
    steps = {s["id"]: s for s in wf["steps"]}
    check = steps["step-1"]
    assert "next" not in check
    assert check["next_branches"] == [
        {"when": "score >= 70", "next": "step-2"},
        {"default": "step-3"},
    ]
    # data 单出边 → next 字符串
    assert steps["step-2"]["next"] == "step-4"


def test_fallback_build_single_data_edge_next_string() -> None:
    g = NodeGraph(root_id="l1-a")
    for nid in ("l1-a", "l1-b"):
        g.add_node(Node(id=nid, layer=NodeLayer.L1_PLANNING, title=nid, description="d"))
    g.add_edge(Edge(id="e1", source="l1-a", target="l1-b"))
    wf = json.loads(WorkflowAdapter()._fallback_build(g)["workflow.json"])
    assert wf["steps"][0]["next"] == "step-2"


def test_validate_rewards_condition_edges() -> None:
    """显式 condition 边提升分支维度得分(空边图的 has_branch 由边触发)"""
    adapter = WorkflowAdapter()
    base = adapter.validate(_branch_graph())
    stripped = NodeGraph.from_dict(_branch_graph().to_dict())
    for e in stripped.edges:
        e.edge_type = "data"
    lower = adapter.validate(stripped)
    assert base.dimensions["idempotency"] > lower.dimensions["idempotency"]


def test_refine_add_edge_condition() -> None:
    """ADD_EDGE patch 带 edge_type/condition → 条件边生效;缺 condition 降级"""
    from goal_studio.core.contract import Patch, PatchOp

    g = NodeGraph(root_id="l1-a")
    for nid in ("l1-a", "l1-b", "l1-c"):
        g.add_node(Node(id=nid, layer=NodeLayer.L1_PLANNING, title=nid, description="d"))
    adapter = WorkflowAdapter()
    g2 = adapter.refine(g, Patch(op=PatchOp.ADD_EDGE, target_node_id="l1-a",
                                 target_node_id_2="l1-b",
                                 slots={"edge_type": "condition", "condition": "ok == true"},
                                 source="text", rationale="t"))
    assert g2.edges[0].edge_type == "condition"
    g3 = adapter.refine(g, Patch(op=PatchOp.ADD_EDGE, target_node_id="l1-a",
                                 target_node_id_2="l1-c",
                                 slots={"edge_type": "condition"},
                                 source="text", rationale="t"))
    assert g3.edges[-1].edge_type == "data"


# ==== StaticLLM: 直接返回预设 JSON(draft 路径) ====

class _StaticLLM:
    """complete_json 返回预设 DAG JSON"""

    name = "static"
    model = "static"

    def complete_json(self, messages, **kwargs):
        return self._payload

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete(self, *a, **k):  # pragma: no cover
        raise NotImplementedError
def test_mock_workflow_draft_produces_condition_edges() -> None:
    """mock 离线路径: workflow DAG 含条件边 → parse 保留语义 → build 编译分支"""
    from goal_studio.llm.mock_provider import MockLLMProvider

    adapter = WorkflowAdapter(llm=MockLLMProvider())
    g = adapter.draft(GoalContract(goal="审批流", constraints=[], success_criteria=[],
                                   product_type=ProductType.WORKFLOW, metadata={}))
    conds = [e for e in g.edges if e.edge_type == "condition"]
    assert len(conds) == 2
    assert all(e.metadata.get("condition") for e in conds)

    wf = json.loads(adapter._fallback_build(g)["workflow.json"])
    branch = next(s for s in wf["steps"] if "分支" in s["name"])
    assert len(branch["next_branches"]) == 2
    assert branch["next_branches"][0]["when"] == "校验通过 == true"
    assert "default" in branch["next_branches"][1]
