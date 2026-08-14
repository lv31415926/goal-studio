"""SkillAdapter: 技能产物适配器 (Phase 1 - LLM 驱动)

把目标转化为 skill SKILL.md + references 的产物。
- draft: LLM 根据目标生成三层 DAG
- refine: LLM 解析自然语言补丁应用到图
- validate: 9 维评估 (LLM + 规则混合)
- build: LLM 根据图生成最终 SKILL.md
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

# === Prompt 模板 ===

DRAFT_SYSTEM_PROMPT = """你是 goal-studio 的技能创作助手。你的任务是把用户目标分解为三层 DAG 工作流。

输出严格的 JSON 格式:
{
  "nodes": [
    {
      "id": "l1-xxx",           // 节点 ID,前缀必须匹配层级 l1-/l2-/l3-
      "layer": 1,               // 层级(1=任务规划/2=Agent分配/3=节点精化)
      "title": "2-5词标题",
      "description": "节点详细描述",
      "node_type": "task",      // task/agent/tool/decision/io
      "parent_id": null,        // L1 为 null; L2 的 parent_id 是所属 L1 节点 ID; L3 的 parent_id 是所属 L2 节点 ID
      "slots": {}               // 节点槽位键值对(L3 必填具体参数)
    }
  ],
  "edges": [
    {"source": "l1-xxx", "target": "l1-yyy"}   // 同层执行顺序边
  ],
  "root_id": "l1-xxx"
}

三层结构要求:
1. L1 任务规划层: 3-7 个节点,覆盖 需求分析→设计→实现→验证→固化,parent_id=null
2. L2 Agent 分配层: 每个 L1 节点下 1-2 个 L2 子节点(parent_id=对应 L1 节点 ID),node_type=agent,描述执行该任务的 Agent 角色
3. L3 节点精化层: 关键 L2 节点下 1-2 个 L3 子节点(parent_id=对应 L2 节点 ID),node_type=tool/decision,slots 必填具体参数(如 prompt 模板/检查规则/工具配置)
4. 节点标题简洁(2-5词),描述具体可执行
5. edges 只连同层节点的执行顺序,无环;父子关系通过 parent_id 表达,不需要 edge
6. 根据 goal 的四要素和约束定制每个节点
7. 只输出 JSON,不要其他文字"""

REFINE_SYSTEM_PROMPT = """你是 goal-studio 的节点精化助手。用户要对 DAG 中的节点做修改,你需要解析用户的自然语言指令。

输出严格的 JSON:
{
  "intent": "create|modify|delete|connect",
  "target_ref": {
    "type": "id|name|spatial|property|pronoun",
    "value": "节点 ID 或名称或'这个'",
    "confidence": 0.0-1.0
  },
  "slots": {
    "要修改的属性名": "新值"
  },
  "ambiguity_flag": false,
  "clarification_options": []
}

规则:
- 如果用户说"这个/那个"且没有明确节点,设 type=pronoun, ambiguity_flag=true
- 如果用户指定了节点名,设 type=name
- 从用户指令提取要改的属性(如"改成异步"→ slots={"execution_mode":"async"})
- 只输出 JSON"""

BUILD_SYSTEM_PROMPT = """你是 goal-studio 的技能生成助手。根据 DAG 工作流生成最终 SKILL.md 技能文件。

输出标准 YAML frontmatter + Markdown 格式的 SKILL.md:
---
name: {技能名}
version: 0.1.0
description: {技能描述}
---
# {技能名} — {一句话描述}

## §1 命令入口
(命令表格)

## §2 工作流
(按 DAG 节点展开)

## §3 关键规则
(从节点 slots 提取)

## §4 失败模式
(常见失败及应对)

要求:
1. 每个 L1 节点变成一个章节
2. 保留节点的 slots 信息作为具体指令
3. 语言简洁,指令具体可执行
4. 直接输出 SKILL.md 内容,不要包裹在代码块里"""


class SkillAdapter(BaseAdapter):
    """技能适配器 (LLM 驱动)"""

    product_type = "skill"
    name = "Skill Adapter"
    description = "把目标转化为可复用的 SKILL.md 技能文件 (LLM 驱动)"

    def __init__(self, llm: BaseLLMProvider | None = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> BaseLLMProvider:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def draft(self, goal: GoalContract) -> NodeGraph:
        """目标 → 三层 DAG (LLM 驱动)

        Args:
            goal: 用户目标契约(四要素 + product_type)

        Returns:
            初始 NodeGraph,L1 有 3-7 个任务节点
        """
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
            # LLM 失败时降级到模板
            return self._fallback_draft(goal, error=str(e))

    def _build_draft_prompt(self, goal: GoalContract) -> str:
        """构建起草 prompt"""
        parts = [f"目标: {goal.goal}"]
        if goal.prerequisites:
            parts.append(f"先决条件: {', '.join(goal.prerequisites)}")
        if goal.constraints:
            parts.append(f"约束: {', '.join(goal.constraints)}")
        if goal.success_criteria:
            parts.append(f"成功标准: {', '.join(goal.success_criteria)}")
        parts.append("\n请生成技能创作的三层 DAG: L1 任务规划(3-7 节点) + L2 Agent 分配(每个 L1 下 1-2 个子节点) + L3 节点精化(关键 L2 下 1-2 个子节点带 slots)。")
        return "\n".join(parts)

    def _parse_dag_response(self, data: dict[str, Any]) -> NodeGraph:
        """解析 LLM 返回的 DAG JSON 为 NodeGraph

        支持三层嵌套(parent_id) + 层次布局:
          L1 横向排列 (y=0)
          L2 在父节点下方 (y=160)
          L3 在父节点下方 (y=320)
        同层子节点在父节点 x 坐标附近展开
        """
        g = NodeGraph()
        nodes_data = data.get("nodes", [])
        edges_data = data.get("edges", [])

        # 第一遍: 创建所有节点(不含位置)
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

        # 第二遍: 层次布局
        self._apply_hierarchical_layout(g)

        # 添加同层边
        for ed in edges_data:
            src = ed.get("source", "")
            tgt = ed.get("target", "")
            if src in g.nodes and tgt in g.nodes:
                try:
                    g.add_edge(
                        Edge(
                            id=f"e-{src}-{tgt}",
                            source=src,
                            target=tgt,
                        )
                    )
                except ValueError:
                    pass  # 跳过会成环的边

        g.root_id = data.get("root_id") or (
            nodes_data[0]["id"] if nodes_data else None
        )
        return g

    def _apply_hierarchical_layout(self, g: NodeGraph) -> None:
        """层次布局算法

        L1: 横向排列,间距 280px,y=0
        L2: 在父节点正下方,y=180,按父节点 x 居中展开
        L3: 在父节点正下方,y=360,按父节点 x 居中展开
        """
        layer_gap = 180
        l1_gap = 280
        child_gap = 180

        # L1 横向排列
        l1_nodes = [n for n in g.nodes.values() if n.layer == NodeLayer.L1_PLANNING]
        # 按拓扑序排列
        try:
            l1_order = g.topological_order(NodeLayer.L1_PLANNING)
            l1_nodes = [g.nodes[nid] for nid in l1_order if nid in g.nodes]
        except ValueError:
            pass

        for i, node in enumerate(l1_nodes):
            node.position = (i * l1_gap, 0.0)

        # L2: 每个父节点下的子节点居中展开
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
                # L3: 每个 L2 下的子节点
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
        """LLM 失败时的三层模板降级"""
        g = NodeGraph()

        # L1 任务规划层 (5 节点)
        l1_tasks = [
            ("l1-req", "需求分析", f"解析目标「{goal.goal[:50]}」,提取四要素"),
            ("l1-design", "方案设计", "设计技能结构与工作流"),
            ("l1-author", "技能体编写", "起草 SKILL.md 主体与 references"),
            ("l1-test", "测试验证", "设计测试 prompt,9 维评估"),
            ("l1-build", "固化产物", "生成最终技能包并导出"),
        ]
        prev: str | None = None
        for tid, title, desc in l1_tasks:
            n = Node(id=tid, layer=NodeLayer.L1_PLANNING, title=title, description=desc)
            g.add_node(n)
            if prev:
                g.add_edge(Edge(id=f"e-{prev}-{n.id}", source=prev, target=n.id))
            prev = tid
        g.root_id = "l1-req"

        # L2 Agent 分配层 (每个 L1 下 1-2 个 Agent)
        l2_specs = [
            ("l2-req-analyst", "l1-req", "需求分析师", "解析用户目标,提取 goal/prerequisites/constraints/success_criteria"),
            ("l2-design-arch", "l1-design", "架构设计师", "设计技能的章节结构和工作流"),
            ("l2-author-writer", "l1-author", "技能撰写者", "起草 SKILL.md 的命令入口、工作流、规则、失败模式"),
            ("l2-author-reviewer", "l1-author", "内容审查者", "校验 SKILL.md 的完整性和可执行性"),
            ("l2-test-runner", "l1-test", "测试执行器", "运行 9 维评估并收集结果"),
            ("l2-build-packer", "l1-build", "产物打包器", "生成最终技能包,导出到 skill 目录"),
        ]
        for tid, parent, title, desc in l2_specs:
            n = Node(
                id=tid, layer=NodeLayer.L2_ALLOCATION, title=title,
                description=desc, node_type="agent", parent_id=parent,
            )
            g.add_node(n)

        # L3 节点精化层 (关键 L2 下的精化节点,带 slots)
        l3_specs: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
            ("l3-req-extract", "l2-req-analyst", "四要素提取器", "从目标中提取四要素信息", "tool",
             {"prompt_template": "从目标中提取 goal/prerequisites/constraints/success_criteria", "output_format": "json"}),
            ("l3-author-skeleton", "l2-author-writer", "章节骨架生成", "生成技能的章节骨架结构", "tool",
             {"sections": ["命令入口", "工作流", "关键规则", "失败模式"], "template": "skill-v1"}),
            ("l3-author-rules", "l2-author-writer", "规则编码器", "将关键规则编码为结构化格式", "decision",
             {"rule_format": "R-{N} {描述}", "min_rules": 3}),
            ("l3-test-9dim", "l2-test-runner", "9维评估器", "运行九维质量评估并判定是否通过", "tool",
             {"dimensions": ["清晰度", "可执行性", "完整性", "失败覆盖", "资源整合", "架构", "性能", "反例", "检查点"],
              "pass_threshold": 70}),
        ]
        for tid, parent, title, desc, ntype, slots in l3_specs:
            n = Node(
                id=tid, layer=NodeLayer.L3_REFINEMENT, title=title,
                description=desc, node_type=ntype, parent_id=parent, slots=slots,
            )
            g.add_node(n)

        # 应用层次布局
        self._apply_hierarchical_layout(g)

        # 记录降级原因
        g.nodes["l1-req"].metadata["fallback"] = True
        g.nodes["l1-req"].metadata["fallback_error"] = error[:200]
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
                    Edge(
                        id=f"e-{patch.target_node_id}-{patch.target_node_id_2}",
                        source=patch.target_node_id,
                        target=patch.target_node_id_2,
                    )
                )
        return new_graph

    def validate(self, graph: NodeGraph) -> Report:
        """9 维评估 (规则 + LLM 混合)

        MVP: 规则评分为主,LLM 评估为辅(可选)。
        """
        dims: dict[str, float] = {}
        issues: list[str] = []
        suggestions: list[str] = []

        # 维度 1: 工作流清晰度 (节点数和边)
        l1_nodes = graph.get_layer(NodeLayer.L1_PLANNING)
        if len(l1_nodes) < 3:
            issues.append("L1 任务节点过少,建议至少 3 个")
            dims["workflow_clarity"] = 45
        elif len(l1_nodes) > 10:
            issues.append("L1 任务节点过多,建议合并")
            dims["workflow_clarity"] = 70
        else:
            dims["workflow_clarity"] = 85

        # 维度 2: 可执行性 (blocked 节点)
        blocked = [n for n in graph.nodes.values() if n.status == NodeStatus.BLOCKED]
        if blocked:
            issues.append(f"{len(blocked)} 个节点处于阻塞状态")
            dims["executability"] = 50
        else:
            dims["executability"] = 80

        # 维度 3: 完整性 (描述覆盖率)
        described = sum(1 for n in graph.nodes.values() if n.description.strip())
        dims["completeness"] = min(100, 50 + (described / max(len(graph.nodes), 1)) * 50)

        # 维度 4: 失败模式覆盖
        has_failure_handling = any(
            "retry" in n.slots or "fallback" in n.slots or "error" in n.description.lower()
            for n in graph.nodes.values()
        )
        dims["failure_coverage"] = 75 if has_failure_handling else 55
        if not has_failure_handling:
            suggestions.append("建议补充失败模式编码(重试/降级)")

        # 维度 5: 资源整合
        has_resource = any(
            "resource" in n.slots or "reference" in n.slots or "tool" in n.node_type
            for n in graph.nodes.values()
        )
        dims["resource_integration"] = 75 if has_resource else 60

        # 维度 6: 架构合理性 (是否有孤立节点)
        isolated = [
            n for n in graph.nodes.values()
            if not any(e.source == n.id or e.target == n.id for e in graph.edges)
            and len(graph.nodes) > 1
        ]
        dims["architecture"] = 80 if not isolated else 60
        if isolated:
            issues.append(f"{len(isolated)} 个孤立节点未连接")

        # 维度 7: 实测表现 (占位,Phase 2 接 LLM 实际运行)
        dims["actual_performance"] = 72

        # 维度 8: 反例与黑名单
        has_anti = any(
            "不要" in n.description or "禁止" in n.description or "avoid" in n.description.lower()
            for n in graph.nodes.values()
        )
        dims["anti_patterns"] = 75 if has_anti else 55
        if not has_anti:
            suggestions.append("建议增加反例与黑名单(不该做的事)")

        # 维度 9: 检查点设计
        has_checkpoint = any(
            "验证" in n.title or "检查" in n.title or "test" in n.title.lower()
            for n in graph.nodes.values()
        )
        dims["checkpoint_design"] = 82 if has_checkpoint else 60
        if not has_checkpoint:
            suggestions.append("建议增加验证/检查点节点")

        score = sum(dims.values()) / len(dims) if dims else 0
        return Report(
            passed=score >= 70,
            score=score,
            dimensions=dims,
            issues=issues,
            suggestions=suggestions,
            evaluator="rule-9dim",
        )

    def build(self, graph: NodeGraph) -> Artifact:
        """DAG → SKILL.md (LLM 驱动)"""
        user_prompt = self._build_build_prompt(graph)
        messages = [
            LLMMessage(role="system", content=BUILD_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]

        try:
            resp = self.llm.complete(messages, temperature=0.5, max_tokens=16384)
            content = resp.content
        except Exception:
            content = self._fallback_build(graph)

        return Artifact(
            product_type=ProductType.SKILL,
            content=content,
            files={"SKILL.md": content},
            metadata={
                "generator": "goal-studio",
                "llm": self.llm.name,
                "llm_model": self.llm.model,
                "node_count": len(graph.nodes),
            },
            graph_snapshot=graph.to_dict(),
        )

    def _build_build_prompt(self, graph: NodeGraph) -> str:
        """构建 build prompt"""
        parts = ["根据以下 DAG 生成 SKILL.md:\n"]
        parts.append("节点:")
        for node in graph.nodes.values():
            slots_str = json.dumps(node.slots, ensure_ascii=False) if node.slots else "{}"
            parts.append(
                f"  - [{node.id}] {node.title} (L{node.layer.value}): {node.description} | slots={slots_str}"
            )
        parts.append("\n边:")
        for e in graph.edges:
            parts.append(f"  - {e.source} → {e.target}")
        parts.append("\n请生成完整的 SKILL.md。")
        return "\n".join(parts)

    def _fallback_build(self, graph: NodeGraph) -> str:
        """LLM 失败时的模板生成"""
        l1 = graph.get_layer(NodeLayer.L1_PLANNING)
        sections = [
            "---",
            "name: generated-skill",
            "version: 0.1.0",
            "description: Auto-generated by goal-studio",
            "---",
            "",
            "# generated-skill — 技能",
            "",
        ]
        for node in l1:
            sections.append(f"## {node.title}")
            sections.append("")
            sections.append(node.description or "(待填充)")
            if node.slots:
                sections.append("")
                sections.append("**参数:**")
                for k, v in node.slots.items():
                    sections.append(f"- {k}: {v}")
            sections.append("")
        return "\n".join(sections)

    def export(self, artifact: Artifact, target: str) -> str:
        """导出技能包"""
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
        """安装到 skill 目录"""
        skill_dir = env.get("skill_dir")
        if not skill_dir:
            return "skill_dir not provided in env"
        from pathlib import Path

        out = Path(skill_dir) / artifact.metadata.get("skill_name", "generated-skill")
        out.mkdir(parents=True, exist_ok=True)
        for path, content in artifact.files.items():
            self._safe_write(out, path, content)
        return f"Installed to {out}"

    def get_schema(self) -> dict[str, Any]:
        return {
            "node_types": ["task", "agent", "tool", "decision"],
            "slots": {
                "task": ["title", "description", "expected_output"],
                "agent": ["role", "tools", "system_prompt"],
                "tool": ["name", "params", "output_schema"],
            },
        }


_ = Any
