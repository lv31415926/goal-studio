"""WorkflowAdapter: 工作流产物适配器 (LLM 驱动)

把目标转化为可执行的工作流定义 (如自动化流程/CI/CD pipeline/业务流程)。
- draft: LLM 生成三层 DAG (触发分析→步骤编排→条件分支→异常处理→部署执行)
- refine: 应用精化补丁到图
- validate: 工作流质量评估 (完备性/可执行性/异常处理/可观测性/幂等性)
- build: LLM 根据图生成工作流定义文件 (YAML/JSON)
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import (
    Artifact,
    GoalContract,
    Patch,
    PatchOp,
    ProductType,
    Report,
)
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer, NodeStatus
from goal_studio.llm import LLMMessage, get_llm
from goal_studio.llm.base import BaseLLMProvider

DRAFT_SYSTEM_PROMPT = """你是 goal-studio 的工作流设计助手。你的任务是把用户目标分解为三层 DAG 工作流,用于生成可执行的工作流定义。

输出严格的 JSON 格式:
{
  "nodes": [
    {
      "id": "l1-xxx", "layer": 1, "title": "2-5词标题",
      "description": "节点详细描述", "node_type": "task",
      "parent_id": null, "slots": {}
    }
  ],
  "edges": [{"source": "l1-xxx", "target": "l1-yyy"}],
  "root_id": "l1-xxx"
}

三层结构要求:
1. L1 任务规划层: 3-7 个节点,覆盖 触发条件分析→步骤编排→条件分支→异常处理→部署执行,parent_id=null
2. L2 Agent 分配层: 每个 L1 节点下 1-2 个 L2 子节点,node_type=agent,描述执行该任务的角色(如流程设计师/集成工程师)
3. L3 节点精化层: 关键 L2 节点下 1-2 个 L3 子节点,node_type=tool/decision,slots 必填具体参数(如触发器类型/步骤定义/重试策略/超时配置)
4. 节点标题简洁(2-5词),描述具体可执行
5. edges 只连同层节点的执行顺序,无环;父子关系通过 parent_id 表达
6. 根据 goal 的四要素和约束定制每个节点
7. 只输出 JSON,不要其他文字"""

BUILD_SYSTEM_PROMPT = """你是 goal-studio 的工作流生成助手。根据 DAG 工作流生成可执行的工作流定义文件。

输出严格的 JSON 格式:
{
  "name": "工作流名称",
  "version": "1.0.0",
  "description": "工作流描述",
  "trigger": {
    "type": "manual|schedule|event|webhook",
    "config": {}
  },
  "steps": [
    {
      "id": "step-1",
      "name": "步骤名称",
      "action": "执行动作",
      "inputs": {},
      "outputs": {},
      "next": "step-2",
      "on_failure": "retry|skip|abort"
    }
  ],
  "error_handling": {
    "retry_policy": {"max_retries": 3, "backoff": "exponential"},
    "timeout": 300,
    "fallback": "通知管理员"
  }
}

要求:
1. 根据 L1 节点生成对应的 steps
2. 根据 L3 节点的 slots 中的触发器/重试/超时配置填充具体参数
3. 步骤之间的 next 关系要匹配 edges
4. 只输出 JSON,不要其他文字"""


class WorkflowAdapter(BaseAdapter):
    """工作流适配器 (LLM 驱动)"""

    product_type = "workflow"
    name = "Workflow Adapter"
    description = "把目标转化为可执行的工作流定义 (LLM 驱动)"

    def __init__(self, llm: BaseLLMProvider | None = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> BaseLLMProvider:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def draft(self, goal: GoalContract) -> NodeGraph:
        """目标 → 三层 DAG (工作流)"""
        user_prompt = self._build_draft_prompt(goal)
        messages = [
            LLMMessage(role="system", content=DRAFT_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]
        try:
            data = self.llm.complete_json(messages, temperature=0.7, max_tokens=16384)
            graph = self._parse_dag_response(data)
            if not graph.nodes:
                return self._fallback_draft(goal, error="LLM returned empty nodes")
            return graph
        except Exception as e:
            return self._fallback_draft(goal, error=str(e))

    def _build_draft_prompt(self, goal: GoalContract) -> str:
        parts = [f"目标: {goal.goal}"]
        if goal.prerequisites:
            parts.append(f"先决条件: {', '.join(goal.prerequisites)}")
        if goal.constraints:
            parts.append(f"约束: {', '.join(goal.constraints)}")
        if goal.success_criteria:
            parts.append(f"成功标准: {', '.join(goal.success_criteria)}")
        parts.append("\n请生成工作流的三层 DAG: L1 任务规划(3-7 节点) + L2 Agent 分配 + L3 节点精化(带触发器/步骤/重试 slots)。")
        return "\n".join(parts)

    def _parse_dag_response(self, data: dict[str, Any]) -> NodeGraph:
        """解析 LLM 返回的 DAG JSON (层次布局)"""
        g = NodeGraph()
        nodes_data = data.get("nodes", [])
        edges_data = data.get("edges", [])

        for i, nd in enumerate(nodes_data):
            layer = NodeLayer(nd.get("layer", 1))
            node = Node(
                id=nd.get("id", f"l{layer.value}-node-{i}"),
                layer=layer,
                title=nd.get("title", f"Node {i}"),
                description=nd.get("description", ""),
                node_type=nd.get("node_type", "task"),
                status=NodeStatus.DRAFT,
                slots=nd.get("slots", {}),
                parent_id=nd.get("parent_id"),
                position=(0.0, 0.0),
            )
            g.add_node(node)

        self._apply_hierarchical_layout(g)

        for ed in edges_data:
            src = ed.get("source", "")
            tgt = ed.get("target", "")
            if src in g.nodes and tgt in g.nodes:
                try:
                    g.add_edge(Edge(id=f"e-{src}-{tgt}", source=src, target=tgt))
                except ValueError:
                    pass

        g.root_id = data.get("root_id") or (nodes_data[0]["id"] if nodes_data else None)
        return g

    def _apply_hierarchical_layout(self, g: NodeGraph) -> None:
        """层次布局 (L1 横向, L2/L3 在父节点下方)"""
        layer_gap = 180
        l1_gap = 280
        child_gap = 180

        l1_nodes = [n for n in g.nodes.values() if n.layer == NodeLayer.L1_PLANNING]
        try:
            l1_order = g.topological_order(NodeLayer.L1_PLANNING)
            l1_nodes = [g.nodes[nid] for nid in l1_order if nid in g.nodes]
        except ValueError:
            pass

        for i, node in enumerate(l1_nodes):
            node.position = (i * l1_gap, 0.0)

        for l1 in l1_nodes:
            l2_children = [
                g.nodes[cid] for cid in l1.children_ids
                if cid in g.nodes and g.nodes[cid].layer == NodeLayer.L2_ALLOCATION
            ]
            if not l2_children:
                continue
            total_w = (len(l2_children) - 1) * child_gap
            start_x = l1.position[0] - total_w / 2
            for j, child in enumerate(l2_children):
                child.position = (start_x + j * child_gap, layer_gap)
                l3_children = [
                    g.nodes[cid] for cid in child.children_ids
                    if cid in g.nodes and g.nodes[cid].layer == NodeLayer.L3_REFINEMENT
                ]
                if not l3_children:
                    continue
                l3_total_w = (len(l3_children) - 1) * (child_gap * 0.7)
                l3_start_x = child.position[0] - l3_total_w / 2
                for k, l3 in enumerate(l3_children):
                    l3.position = (l3_start_x + k * (child_gap * 0.7), layer_gap * 2)

    def _fallback_draft(self, goal: GoalContract, error: str = "") -> NodeGraph:
        """LLM 失败时的三层模板降级 (工作流)"""
        g = NodeGraph()

        l1_tasks = [
            ("l1-trigger", "触发分析", f"分析「{goal.goal[:50]}」的触发条件和入口"),
            ("l1-orchestrate", "步骤编排", "编排工作流步骤和执行顺序"),
            ("l1-branch", "条件分支", "设计条件判断和分支逻辑"),
            ("l1-error", "异常处理", "设计重试、降级、回滚策略"),
            ("l1-deploy", "部署执行", "配置部署环境和执行参数"),
        ]
        prev: str | None = None
        for tid, title, desc in l1_tasks:
            n = Node(id=tid, layer=NodeLayer.L1_PLANNING, title=title, description=desc)
            g.add_node(n)
            if prev:
                g.add_edge(Edge(id=f"e-{prev}-{n.id}", source=prev, target=n.id))
            prev = tid
        g.root_id = "l1-trigger"

        l2_specs = [
            ("l2-trigger-analyst", "l1-trigger", "触发分析师", "分析触发类型、入口条件、前置检查"),
            ("l2-orchestrate-designer", "l1-orchestrate", "流程设计师", "设计步骤序列、数据流、依赖关系"),
            ("l2-branch-designer", "l1-branch", "分支设计师", "设计条件判断、分支路径、合并点"),
            ("l2-error-engineer", "l1-error", "异常工程师", "设计重试策略、降级方案、告警机制"),
            ("l2-deploy-devops", "l1-deploy", "部署工程师", "配置执行环境、资源限制、监控"),
        ]
        for tid, parent, title, desc in l2_specs:
            n = Node(id=tid, layer=NodeLayer.L2_ALLOCATION, title=title,
                     description=desc, node_type="agent", parent_id=parent)
            g.add_node(n)

        l3_specs: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
            ("l3-trigger-config", "l2-trigger-analyst", "触发器配置", "配置触发端点和认证方式", "tool",
             {"type": "webhook", "endpoint": "/api/trigger", "auth": "bearer", "method": "POST"}),
            ("l3-orchestrate-steps", "l2-orchestrate-designer", "步骤定义生成", "设计步骤数量和数据流", "tool",
             {"step_count": 5, "parallel": False, "data_flow": "sequential", "timeout_per_step": 60}),
            ("l3-error-retry", "l2-error-engineer", "重试策略配置", "配置重试次数和退避策略", "decision",
             {"max_retries": 3, "backoff": "exponential", "base_delay": 5, "max_delay": 300}),
            ("l3-deploy-monitor", "l2-deploy-devops", "监控配置", "配置监控指标和告警阈值", "tool",
             {"metrics": ["duration", "success_rate", "error_count"], "alert_threshold": 0.1, "log_level": "info"}),
        ]
        for tid, parent, title, desc, ntype, slots in l3_specs:
            n = Node(id=tid, layer=NodeLayer.L3_REFINEMENT, title=title,
                     description=desc, node_type=ntype, parent_id=parent, slots=slots)
            g.add_node(n)

        self._apply_hierarchical_layout(g)
        g.nodes["l1-trigger"].metadata["fallback"] = True
        g.nodes["l1-trigger"].metadata["fallback_error"] = error[:200]
        return g

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        """应用精化补丁"""
        new_graph = NodeGraph.from_dict(graph.to_dict())
        new_graph.version += 1

        if patch.op == PatchOp.UPDATE_NODE and patch.target_node_id:
            node = new_graph.nodes.get(patch.target_node_id)
            if node:
                node.slots.update(patch.slots)
                if "title" in patch.slots and isinstance(patch.slots["title"], str):
                    node.title = patch.slots["title"]
                if "description" in patch.slots and isinstance(patch.slots["description"], str):
                    node.description = patch.slots["description"]
        elif patch.op == PatchOp.ADD_NODE and patch.new_node:
            node = Node(
                id=patch.new_node.get("id", f"n-{uuid.uuid4().hex[:8]}"),
                layer=NodeLayer(patch.new_node.get("layer", 2)),
                title=patch.new_node.get("title", "New Node"),
                description=patch.new_node.get("description", ""),
                slots=patch.new_node.get("slots", {}),
                parent_id=patch.target_node_id,
            )
            new_graph.add_node(node)
        elif patch.op == PatchOp.REMOVE_NODE and patch.target_node_id:
            new_graph.remove_node(patch.target_node_id)
        elif patch.op == PatchOp.ADD_EDGE and patch.target_node_id and patch.target_node_id_2:
            if patch.target_node_id in new_graph.nodes and patch.target_node_id_2 in new_graph.nodes:
                new_graph.add_edge(
                    Edge(id=f"e-{patch.target_node_id}-{patch.target_node_id_2}",
                         source=patch.target_node_id, target=patch.target_node_id_2)
                )
        return new_graph

    def validate(self, graph: NodeGraph) -> Report:
        """工作流质量评估 (5 维)"""
        dims: dict[str, float] = {}
        issues: list[str] = []
        suggestions: list[str] = []

        # 维度 1: 完备性 (L1 节点数)
        l1_nodes = graph.get_layer(NodeLayer.L1_PLANNING)
        if len(l1_nodes) < 3:
            issues.append("L1 任务节点过少,工作流不完整")
            dims["completeness"] = 45
        elif len(l1_nodes) > 8:
            issues.append("L1 任务节点过多,建议合并")
            dims["completeness"] = 65
        else:
            dims["completeness"] = 85

        # 维度 2: 可执行性 (是否有触发器配置)
        has_trigger = any(
            "trigger" in n.title.lower() or "触发" in n.title or "type" in n.slots
            for n in graph.nodes.values()
        )
        dims["executability"] = 85 if has_trigger else 55
        if not has_trigger:
            suggestions.append("建议补充触发器配置")

        # 维度 3: 异常处理 (是否有异常/重试节点)
        has_error_handling = any(
            "异常" in n.title or "error" in n.title.lower() or "retry" in str(n.slots).lower()
            or "重试" in n.title
            for n in graph.nodes.values()
        )
        dims["error_handling"] = 82 if has_error_handling else 45
        if not has_error_handling:
            issues.append("缺少异常处理节点,工作流健壮性不足")
            suggestions.append("建议增加重试策略和降级方案")

        # 维度 4: 可观测性 (是否有监控配置)
        has_monitor = any(
            "监控" in n.title or "monitor" in n.title.lower() or "metrics" in n.slots
            for n in graph.nodes.values()
        )
        dims["observability"] = 80 if has_monitor else 55
        if not has_monitor:
            suggestions.append("建议增加监控和告警配置")

        # 维度 5: 幂等性 (描述覆盖率 + 条件分支)
        has_branch = any(
            "分支" in n.title or "branch" in n.title.lower() or "condition" in n.slots
            for n in graph.nodes.values()
        )
        described = sum(1 for n in graph.nodes.values() if n.description.strip())
        base_score = min(100, 50 + (described / max(len(graph.nodes), 1)) * 50)
        dims["idempotency"] = (base_score + (75 if has_branch else 55)) / 2

        score = sum(dims.values()) / len(dims) if dims else 0
        return Report(
            passed=score >= 70,
            score=score,
            dimensions=dims,
            issues=issues,
            suggestions=suggestions,
            evaluator="workflow-5dim",
        )

    def build(self, graph: NodeGraph) -> Artifact:
        """DAG → 工作流定义文件 (LLM 驱动)"""
        user_prompt = self._build_build_prompt(graph)
        messages = [
            LLMMessage(role="system", content=BUILD_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]

        try:
            data = self.llm.complete_json(messages, temperature=0.5, max_tokens=16384)
            content = json.dumps(data, ensure_ascii=False, indent=2)
            files = {"workflow.json": content}
        except Exception:
            files = self._fallback_build(graph)
            content = files["workflow.json"]

        return Artifact(
            product_type=ProductType.WORKFLOW,
            content=content,
            files=files,
            metadata={
                "generator": "goal-studio",
                "llm": self.llm.name,
                "llm_model": self.llm.model,
                "node_count": len(graph.nodes),
                "format": "json",
            },
            graph_snapshot=graph.to_dict(),
        )

    def _build_build_prompt(self, graph: NodeGraph) -> str:
        parts = ["根据以下 DAG 生成工作流定义:\n"]
        parts.append("节点:")
        for node in graph.nodes.values():
            slots_str = json.dumps(node.slots, ensure_ascii=False) if node.slots else "{}"
            parts.append(f"  - [{node.id}] {node.title} (L{node.layer.value}): {node.description} | slots={slots_str}")
        parts.append("\n边:")
        for e in graph.edges:
            parts.append(f"  - {e.source} → {e.target}")
        parts.append("\n请生成完整的工作流定义 JSON。")
        return "\n".join(parts)

    def _fallback_build(self, graph: NodeGraph) -> dict[str, str]:
        """LLM 失败时的模板生成"""
        l1 = graph.get_layer(NodeLayer.L1_PLANNING)
        steps = []
        for i, node in enumerate(l1):
            step: dict[str, Any] = {
                "id": f"step-{i+1}",
                "name": node.title,
                "action": node.description,
                "inputs": {},
                "outputs": {},
                "next": f"step-{i+2}" if i < len(l1) - 1 else None,
                "on_failure": "retry",
            }
            steps.append(step)

        # 收集 L3 配置
        trigger_config = {"type": "manual", "config": {}}
        retry_config = {"max_retries": 3, "backoff": "exponential"}
        for n in graph.nodes.values():
            if n.layer == NodeLayer.L3_REFINEMENT:
                if "type" in n.slots and ("endpoint" in n.slots or "method" in n.slots):
                    trigger_config = {"type": n.slots.get("type", "manual"), "config": n.slots}
                if "max_retries" in n.slots:
                    retry_config = {
                        "max_retries": n.slots.get("max_retries", 3),
                        "backoff": n.slots.get("backoff", "exponential"),
                    }

        workflow = {
            "name": "generated-workflow",
            "version": "1.0.0",
            "description": "Auto-generated by goal-studio",
            "trigger": trigger_config,
            "steps": steps,
            "error_handling": {
                "retry_policy": retry_config,
                "timeout": 300,
                "fallback": "通知管理员",
            },
        }
        return {"workflow.json": json.dumps(workflow, ensure_ascii=False, indent=2)}

    def export(self, artifact: Artifact, target: str) -> str:
        """导出工作流定义"""
        from pathlib import Path
        from urllib.parse import urlparse

        parsed = urlparse(target)
        if parsed.scheme == "file":
            out_dir = Path(parsed.path)
            out_dir.mkdir(parents=True, exist_ok=True)
            for path, content in artifact.files.items():
                self._safe_write(out_dir, path, content)
            return str(out_dir)
        raise ValueError(f"Unsupported export target: {target}")

    def install(self, artifact: Artifact, env: dict[str, str]) -> str:
        """安装到工作流引擎"""
        engine = env.get("engine", "unknown")
        engine_dir = env.get("workflow_dir")
        if not engine_dir:
            return "workflow_dir not provided in env"
        from pathlib import Path

        out = Path(engine_dir)
        out.mkdir(parents=True, exist_ok=True)
        for path, content in artifact.files.items():
            self._safe_write(out, path, content)
        return f"Installed to {engine} engine at {out}"

    def get_schema(self) -> dict[str, Any]:
        return {
            "node_types": ["task", "agent", "tool", "decision"],
            "slots": {
                "task": ["title", "description", "expected_output"],
                "agent": ["role", "responsibility"],
                "tool": ["trigger_type", "step_config", "retry_policy", "monitor_config"],
                "decision": ["condition", "branches", "default"],
            },
        }


_ = Any
