"""WorkflowAdapter: 工作流产物适配器 (Phase 2 实现)"""
from __future__ import annotations

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, ProductType, Report
from goal_studio.core.node_graph import NodeGraph


class WorkflowAdapter(BaseAdapter):
    product_type = "workflow"
    name = "Workflow Adapter"
    description = "把目标转化为可执行工作流(n8n/LangChain 兼容)"

    def draft(self, goal: GoalContract) -> NodeGraph:
        from goal_studio.core.node_graph import Node, NodeLayer
        g = NodeGraph()
        g.add_node(Node(id="l1-trigger", layer=NodeLayer.L1_PLANNING, title="触发器", description="工作流入口"))
        g.add_node(Node(id="l1-steps", layer=NodeLayer.L1_PLANNING, title="执行步骤", description="步骤编排"))
        g.add_node(Node(id="l1-output", layer=NodeLayer.L1_PLANNING, title="输出", description="结果输出"))
        g.root_id = "l1-trigger"
        return g

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        return graph

    def validate(self, graph: NodeGraph) -> Report:
        return Report(passed=True, score=75, evaluator="workflow-stub", issues=["WorkflowAdapter.validate 待实现"])

    def build(self, graph: NodeGraph) -> Artifact:
        import json
        return Artifact(
            product_type=ProductType.WORKFLOW,
            content=json.dumps({"nodes": [], "edges": []}, ensure_ascii=False),
            files={"workflow.json": "{}"},
            metadata={"generator": "goal-studio"},
            graph_snapshot=graph.to_dict(),
        )

    def export(self, artifact: Artifact, target: str) -> str:
        return f"WorkflowAdapter.export 待实现 (target={target})"
