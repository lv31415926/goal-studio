"""ExecutionEngine 单元测试: 拓扑执行 / 条件分支 / 接地失败 / 级联跳过"""
from __future__ import annotations

import pytest

from goal_studio.core.execution import (
    ConditionKeyError,
    ConditionRouter,
    DefaultNodeExecutor,
    ExecutionEngine,
    ExecutionStatus,
    evaluate_condition,
    resolve_key,
)
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer, NodeStatus


def _node(nid: str, title: str = "", slots: dict | None = None,
          status: NodeStatus = NodeStatus.READY) -> Node:
    return Node(
        id=nid, layer=NodeLayer.L1_PLANNING, title=title or nid,
        status=status, slots=slots or {},
    )


def _edge(src: str, tgt: str, cond: str | None = None) -> Edge:
    return Edge(
        id=f"{src}->{tgt}", source=src, target=tgt,
        edge_type="condition" if cond else "data",
        metadata={"condition": cond} if cond else {},
    )


def _linear_graph() -> NodeGraph:
    g = NodeGraph(root_id="a")
    for nid in ("a", "b", "c"):
        g.add_node(_node(nid))
    g.add_edge(_edge("a", "b"))
    g.add_edge(_edge("b", "c"))
    return g


class TestEngine:
    def test_linear_all_success(self) -> None:
        s = ExecutionEngine().run(_linear_graph())
        assert s.passed
        assert s.stats == {"total": 3, "executed": 3, "skipped": 0,
                           "failed": 0, "pending": 0}
        assert all(r.status == ExecutionStatus.SUCCESS for r in s.records)

    def test_l2_l3_no_edges_are_entries(self) -> None:
        """L2/L3 无同层边 → 自动成为入口节点被执行"""
        g = NodeGraph(root_id="l1")
        g.add_node(_node("l1"))
        g.add_node(Node(id="l2", layer=NodeLayer.L2_ALLOCATION, title="agent"))
        g.add_node(Node(id="l3", layer=NodeLayer.L3_REFINEMENT, title="tool"))
        s = ExecutionEngine().run(g)
        assert s.passed
        assert s.stats["executed"] == 3

    def test_condition_branch_taken_and_skipped(self) -> None:
        """A 发布 校验通过=True → true 分支执行,false 分支下游 SKIPPED"""
        g = NodeGraph(root_id="a")
        g.add_node(_node("a", "校验", slots={"校验通过": True}))
        for nid in ("b", "d", "c", "e"):
            g.add_node(_node(nid))
        g.add_edge(_edge("a", "b", "校验通过 == true"))
        g.add_edge(_edge("a", "c", "校验通过 == false"))
        g.add_edge(_edge("b", "d"))
        g.add_edge(_edge("c", "e"))
        s = ExecutionEngine().run(g)
        assert s.passed
        assert s.stats == {"total": 5, "executed": 3, "skipped": 2,
                           "failed": 0, "pending": 0}
        by_id = {r.node_id: r for r in s.records}
        assert by_id["d"].status == ExecutionStatus.SUCCESS
        assert by_id["c"].status == ExecutionStatus.SKIPPED
        assert by_id["e"].status == ExecutionStatus.SKIPPED
        assert "分支未命中" in by_id["e"].detail

    def test_initial_context_drives_branch(self) -> None:
        """初始 context(触发变量)同样可驱动条件分支"""
        g = NodeGraph(root_id="a")
        g.add_node(_node("a"))
        g.add_node(_node("b"))
        g.add_node(_node("c"))
        g.add_edge(_edge("a", "b", "审批通过 == true"))
        g.add_edge(_edge("a", "c", "审批通过 == false"))
        s = ExecutionEngine().run(g, initial_context={"审批通过": True})
        assert s.passed
        by_id = {r.node_id: r for r in s.records}
        assert by_id["b"].status == ExecutionStatus.SUCCESS
        assert by_id["c"].status == ExecutionStatus.SKIPPED

    def test_unresolved_condition_key_aborts(self) -> None:
        """条件键无任何节点发布 → ABORTED + 诊断(grounded validation 核心)"""
        g = NodeGraph(root_id="a")
        g.add_node(_node("a"))
        g.add_node(_node("b"))
        g.add_edge(_edge("a", "b", "不存在的键 == true"))
        s = ExecutionEngine().run(g)
        assert s.status == ExecutionStatus.ABORTED
        assert s.error is not None and "不存在的键" in s.error
        assert "条件接地失败" in s.error
        # 未触达节点计入 pending,统计自洽
        assert s.stats["pending"] >= 1
        assert sum(s.stats[k] for k in ("executed", "skipped", "failed", "pending")) \
            == s.stats["total"]

    def test_failed_node_cascades_skip(self) -> None:
        engine = ExecutionEngine(
            executor=DefaultNodeExecutor(fail_nodes={"b"})
        )
        s = engine.run(_linear_graph())
        assert s.status == ExecutionStatus.FAILED
        by_id = {r.node_id: r for r in s.records}
        assert by_id["b"].status == ExecutionStatus.FAILED
        assert by_id["b"].error is not None and "模拟失败" in by_id["b"].error
        assert by_id["c"].status == ExecutionStatus.SKIPPED

    def test_blocked_node_fails(self) -> None:
        g = NodeGraph(root_id="a")
        g.add_node(_node("a", status=NodeStatus.BLOCKED))
        s = ExecutionEngine().run(g)
        assert s.status == ExecutionStatus.FAILED
        assert "blocked" in (s.records[0].error or "")

    def test_cycle_aborts(self) -> None:
        """成环节点 → 拓扑排序抛错 → ABORTED(不抛异常给调用方)"""
        g = NodeGraph(root_id="a")
        g.add_node(_node("a"))
        g.add_node(_node("b"))
        # 绕过 add_edge 环校验,直接构造(模拟持久化脏数据)
        g.edges.append(Edge(id="e1", source="a", target="b"))
        g.edges.append(Edge(id="e2", source="b", target="a"))
        g._build_adjacency()  # noqa: SLF001 — 测试构造脏图
        s = ExecutionEngine().run(g)
        assert s.status == ExecutionStatus.ABORTED
        assert "环" in (s.error or "")

    def test_empty_graph_aborts(self) -> None:
        s = ExecutionEngine().run(NodeGraph())
        assert s.status == ExecutionStatus.ABORTED

    def test_join_node_runs_if_any_in_edge_activated(self) -> None:
        """合流语义: 任一入边激活即可运行"""
        g = NodeGraph(root_id="a")
        g.add_node(_node("a"))
        g.add_node(_node("b"))
        g.add_node(_node("join"))
        g.add_edge(_edge("a", "join"))
        g.add_edge(_edge("b", "join"))
        s = ExecutionEngine().run(g)
        assert s.passed
        by_id = {r.node_id: r for r in s.records}
        assert by_id["join"].status == ExecutionStatus.SUCCESS

    def test_context_publish_keys(self) -> None:
        """输出发布: node.id / 归一化标题 / 扁平 slot 键"""
        g = NodeGraph(root_id="a")
        g.add_node(_node("a", "质量检查", slots={"分数": 88}))
        s = ExecutionEngine().run(g)
        assert s.context["a"]["分数"] == 88
        assert s.context["质量检查"]["分数"] == 88
        assert s.context["分数"] == 88


class TestConditionEvaluation:
    def test_numeric_compare(self) -> None:
        ok, detail = evaluate_condition("分数 >= 60", {"分数": 75})
        assert ok and "75" in detail

    def test_numeric_compare_false(self) -> None:
        ok, _ = evaluate_condition("分数 < 60", {"分数": 75})
        assert not ok

    def test_bool_string_coercion(self) -> None:
        ok, _ = evaluate_condition("校验通过 == true", {"校验通过": True})
        assert ok
        ok2, _ = evaluate_condition("校验通过 == false", {"校验通过": True})
        assert not ok2

    def test_bare_truthiness(self) -> None:
        ok, _ = evaluate_condition("审批通过", {"审批通过": True})
        assert ok
        ok2, _ = evaluate_condition("审批通过", {"审批通过": False})
        assert not ok2

    def test_quoted_string(self) -> None:
        ok, _ = evaluate_condition('环境 == "prod"', {"环境": "prod"})
        assert ok

    def test_normalized_key(self) -> None:
        """「校验_通过」可命中上下文键「校验通过」"""
        ok, _ = evaluate_condition("校验_通过 == true", {"校验通过": True})
        assert ok

    def test_unresolved_raises(self) -> None:
        with pytest.raises(ConditionKeyError):
            evaluate_condition("缺失键 == true", {"别的": 1})

    def test_ambiguous_substring_raises(self) -> None:
        with pytest.raises(ConditionKeyError):
            resolve_key("校验", {"校验输入": 1, "校验输出": 2})

    def test_unique_substring_match(self) -> None:
        assert resolve_key("校验", {"校验结果": True}) is True


class TestRouter:
    def test_data_edge_always_active(self) -> None:
        assert ConditionRouter().route(_edge("a", "b"), {})

    def test_condition_edge_without_text_defaults_true(self) -> None:
        e = Edge(id="x", source="a", target="b", edge_type="condition")
        assert ConditionRouter().route(e, {})

    def test_condition_edge_evaluates(self) -> None:
        e = _edge("a", "b", "分数 >= 60")
        assert ConditionRouter().route(e, {"分数": 80})
        assert not ConditionRouter().route(e, {"分数": 40})

    def test_label_fallback(self) -> None:
        """condition 元数据缺失时回退 label 求值"""
        e = Edge(id="x", source="a", target="b",
                 edge_type="condition", label="审批通过 == true")
        assert ConditionRouter().route(e, {"审批通过": True})


class TestSummary:
    def test_to_dict_roundtrip(self) -> None:
        s = ExecutionEngine().run(_linear_graph())
        d = s.to_dict()
        assert d["passed"] is True
        assert d["status"] == "success"
        assert len(d["records"]) == 3
        assert d["records"][0]["node_id"] == "a"
