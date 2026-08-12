"""CodeAdapter: 代码模块适配器 (Phase 2 实现)"""
from __future__ import annotations

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, ProductType, Report
from goal_studio.core.node_graph import NodeGraph


class CodeAdapter(BaseAdapter):
    product_type = "code"
    name = "Code Adapter"
    description = "把目标转化为代码模块(带 lint 验证)"

    def draft(self, goal: GoalContract) -> NodeGraph:
        from goal_studio.core.node_graph import Node, NodeLayer
        g = NodeGraph()
        g.add_node(Node(id="l1-design", layer=NodeLayer.L1_PLANNING, title="架构设计", description="模块划分"))
        g.add_node(Node(id="l1-impl", layer=NodeLayer.L1_PLANNING, title="代码实现", description="编写模块代码"))
        g.add_node(Node(id="l1-test", layer=NodeLayer.L1_PLANNING, title="测试", description="单元测试"))
        g.root_id = "l1-design"
        return g

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        return graph  # Phase 2

    def validate(self, graph: NodeGraph) -> Report:
        return Report(passed=True, score=75, evaluator="lint-stub", issues=["CodeAdapter.validate 待实现"])

    def build(self, graph: NodeGraph) -> Artifact:
        return Artifact(
            product_type=ProductType.CODE,
            content="# CodeAdapter.build 待实现",
            files={"main.py": "# TODO"},
            metadata={"generator": "goal-studio"},
            graph_snapshot=graph.to_dict(),
        )

    def export(self, artifact: Artifact, target: str) -> str:
        return f"CodeAdapter.export 待实现 (target={target})"
