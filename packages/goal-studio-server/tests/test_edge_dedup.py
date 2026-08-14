"""Edge 去重测试 (P-LOW-2-10, 专家 5 建议)

P-LOW-2-2 的 add_edge 去重将在 PR-E 实施,此处先测试去重行为。
本测试在 PR-D 阶段会因去重逻辑未实现而预期失败,PR-E 实施后通过。
"""
from __future__ import annotations

import pytest


def _make_graph():
    from goal_studio.core.node_graph import NodeGraph, Node, NodeLayer
    g = NodeGraph()
    g.add_node(Node(id="a", layer=NodeLayer.L1_PLANNING, title="A", description=""))
    g.add_node(Node(id="b", layer=NodeLayer.L1_PLANNING, title="B", description=""))
    return g


def test_add_edge_dedup_same_id():
    """add 两次同 id 边,edges 长度应为 1"""
    from goal_studio.core.node_graph import Edge
    g = _make_graph()
    g.add_edge(Edge(id="e1", source="a", target="b"))
    g.add_edge(Edge(id="e1", source="a", target="b"))  # 同 id
    assert len(g.edges) == 1, "同 id 边应去重"


def test_add_edge_dedup_same_endpoints():
    """add 两次同 source/target/type 边(不同 id),edges 长度应为 1"""
    from goal_studio.core.node_graph import Edge
    g = _make_graph()
    g.add_edge(Edge(id="e1", source="a", target="b"))
    g.add_edge(Edge(id="e2", source="a", target="b"))  # 同端点不同 id
    assert len(g.edges) == 1, "同端点边应去重"


def test_add_edge_different_endpoints_not_deduped():
    """不同端点的边不应被去重"""
    from goal_studio.core.node_graph import Node, NodeLayer, Edge
    g = _make_graph()
    g.add_node(Node(id="c", layer=NodeLayer.L1_PLANNING, title="C", description=""))
    g.add_edge(Edge(id="e1", source="a", target="b"))
    g.add_edge(Edge(id="e2", source="a", target="c"))
    assert len(g.edges) == 2
