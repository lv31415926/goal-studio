"""DocAdapter: 文档产物适配器 (Phase 2 实现)"""
from __future__ import annotations

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, ProductType, Report
from goal_studio.core.node_graph import NodeGraph


class DocAdapter(BaseAdapter):
    product_type = "doc"
    name = "Doc Adapter"
    description = "把目标转化为结构化文档"

    def draft(self, goal: GoalContract) -> NodeGraph:
        from goal_studio.core.node_graph import Node, NodeLayer
        g = NodeGraph()
        g.add_node(Node(id="l1-outline", layer=NodeLayer.L1_PLANNING, title="文档大纲", description="章节规划"))
        g.add_node(Node(id="l1-content", layer=NodeLayer.L1_PLANNING, title="内容撰写", description="逐章撰写"))
        g.add_node(Node(id="l1-review", layer=NodeLayer.L1_PLANNING, title="审校", description="润色审校"))
        g.root_id = "l1-outline"
        return g

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        return graph

    def validate(self, graph: NodeGraph) -> Report:
        return Report(passed=True, score=75, evaluator="doc-stub", issues=["DocAdapter.validate 待实现"])

    def build(self, graph: NodeGraph) -> Artifact:
        return Artifact(
            product_type=ProductType.DOC,
            content="# DocAdapter.build 待实现",
            files={"README.md": "# TODO"},
            metadata={"generator": "goal-studio"},
            graph_snapshot=graph.to_dict(),
        )

    def export(self, artifact: Artifact, target: str) -> str:
        return f"DocAdapter.export 待实现 (target={target})"
