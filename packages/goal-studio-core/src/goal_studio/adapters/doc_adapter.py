"""DocAdapter: 文档产物适配器 (LLM 驱动)

把目标转化为结构化文档 (如方案文档/技术文档/报告)。
- draft: LLM 生成三层 DAG (调研→大纲→起草→审校→定稿)
- refine: 应用精化补丁到图
- validate: 文档质量评估 (结构/完整性/可读性/准确性/格式)
- build: LLM 根据图生成最终文档
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

DRAFT_SYSTEM_PROMPT = """你是 goal-studio 的文档创作助手。你的任务是把用户目标分解为三层 DAG 工作流,用于生成结构化文档。

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
1. L1 任务规划层: 3-7 个节点,覆盖 主题调研→大纲设计→内容起草→审校修订→定稿导出,parent_id=null
2. L2 Agent 分配层: 每个 L1 节点下 1-2 个 L2 子节点,node_type=agent,描述执行该任务的角色(如研究员/撰稿人/编辑)
3. L3 节点精化层: 关键 L2 节点下 1-2 个 L3 子节点,node_type=tool,slots 必填具体参数(如章节标题/字数要求/引用格式/审校清单)
4. 节点标题简洁(2-5词),描述具体可执行
5. edges 只连同层节点的执行顺序,无环;父子关系通过 parent_id 表达
6. 根据 goal 的四要素和约束定制每个节点
7. 只输出 JSON,不要其他文字"""

BUILD_SYSTEM_PROMPT = """你是 goal-studio 的文档生成助手。根据 DAG 工作流生成完整的结构化文档。

输出 Markdown 格式的文档内容,包含:
1. 标题和元信息
2. 按 L1 节点对应的章节展开
3. 根据 L3 节点的 slots 中的章节标题/字数要求填充具体内容
4. 语言专业、逻辑清晰、结构完整
5. 直接输出 Markdown 文档,不要包裹在代码块里"""


class DocAdapter(BaseAdapter):
    """文档适配器 (LLM 驱动)"""

    product_type = "doc"
    name = "Doc Adapter"
    description = "把目标转化为结构化文档 (LLM 驱动)"

    def __init__(self, llm: BaseLLMProvider | None = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> BaseLLMProvider:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def draft(self, goal: GoalContract) -> NodeGraph:
        """目标 → 三层 DAG (文档)"""
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
        parts.append("\n请生成文档创作的三层 DAG: L1 任务规划(3-7 节点) + L2 Agent 分配 + L3 节点精化(带章节/字数 slots)。")
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
        """LLM 失败时的三层模板降级 (文档)"""
        g = NodeGraph()

        l1_tasks = [
            ("l1-research", "主题调研", f"调研「{goal.goal[:50]}」,收集资料和关键信息"),
            ("l1-outline", "大纲设计", "设计文档章节结构和逻辑脉络"),
            ("l1-draft", "内容起草", "按大纲撰写各章节内容"),
            ("l1-review", "审校修订", "审校内容准确性、逻辑性、语言质量"),
            ("l1-finalize", "定稿导出", "格式化、排版、导出最终文档"),
        ]
        prev: str | None = None
        for tid, title, desc in l1_tasks:
            n = Node(id=tid, layer=NodeLayer.L1_PLANNING, title=title, description=desc)
            g.add_node(n)
            if prev:
                g.add_edge(Edge(id=f"e-{prev}-{n.id}", source=prev, target=n.id))
            prev = tid
        g.root_id = "l1-research"

        l2_specs = [
            ("l2-research-analyst", "l1-research", "研究员", "收集资料、提取关键信息、整理参考文献"),
            ("l2-outline-designer", "l1-outline", "大纲设计师", "设计章节层级、逻辑脉络、内容分配"),
            ("l2-draft-writer", "l1-draft", "撰稿人", "按大纲撰写各章节正文内容"),
            ("l2-review-editor", "l1-review", "审校编辑", "校对事实、逻辑、语言、格式"),
            ("l2-finalize-formatter", "l1-finalize", "排版师", "格式化、生成目录、导出多格式"),
        ]
        for tid, parent, title, desc in l2_specs:
            n = Node(id=tid, layer=NodeLayer.L2_ALLOCATION, title=title,
                     description=desc, node_type="agent", parent_id=parent)
            g.add_node(n)

        l3_specs: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
            ("l3-research-sources", "l2-research-analyst", "资料源配置", "配置资料源和引用格式", "tool",
             {"sources": ["官方文档", "学术论文", "行业报告"], "min_refs": 5, "citation_format": "GB/T 7714"}),
            ("l3-outline-structure", "l2-outline-designer", "章节结构生成", "生成章节层级和内容分配", "tool",
             {"sections": ["引言", "背景", "主体", "结论", "参考文献"], "depth": 3, "word_balance": True}),
            ("l3-draft-style", "l2-draft-writer", "写作风格配置", "配置写作风格和字数目标", "tool",
             {"style": "专业", "tone": "客观", "target_words": 5000, "language": "zh-CN"}),
            ("l3-review-checklist", "l2-review-editor", "审校清单", "设定审校检查项和通过阈值", "decision",
             {"checklist": ["事实准确", "逻辑连贯", "语言通顺", "格式规范", "引用完整"], "pass_threshold": 0.9}),
        ]
        for tid, parent, title, desc, ntype, slots in l3_specs:
            n = Node(id=tid, layer=NodeLayer.L3_REFINEMENT, title=title,
                     description=desc, node_type=ntype, parent_id=parent, slots=slots)
            g.add_node(n)

        self._apply_hierarchical_layout(g)
        g.nodes["l1-research"].metadata["fallback"] = True
        g.nodes["l1-research"].metadata["fallback_error"] = error[:200]
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
        """文档质量评估 (5 维)"""
        dims: dict[str, float] = {}
        issues: list[str] = []
        suggestions: list[str] = []

        # 维度 1: 结构完整性 (L1 节点数)
        l1_nodes = graph.get_layer(NodeLayer.L1_PLANNING)
        if len(l1_nodes) < 3:
            issues.append("L1 任务节点过少,文档流程不完整")
            dims["structure"] = 45
        elif len(l1_nodes) > 8:
            issues.append("L1 任务节点过多,建议合并")
            dims["structure"] = 65
        else:
            dims["structure"] = 85

        # 维度 2: 内容完整性 (是否有章节结构 slots)
        has_sections = any(
            "sections" in n.slots or "chapters" in n.slots
            for n in graph.nodes.values()
        )
        dims["completeness"] = 85 if has_sections else 55
        if not has_sections:
            suggestions.append("建议在 L3 节点补充章节结构信息")

        # 维度 3: 调研深度 (是否有调研节点)
        has_research = any(
            "调研" in n.title or "research" in n.title.lower() or "sources" in n.slots
            for n in graph.nodes.values()
        )
        dims["research_depth"] = 82 if has_research else 50
        if not has_research:
            suggestions.append("建议增加主题调研环节,提升内容深度")

        # 维度 4: 审校严谨 (是否有审校节点)
        has_review = any(
            "审校" in n.title or "review" in n.title.lower() or "checklist" in n.slots
            for n in graph.nodes.values()
        )
        dims["review_rigor"] = 80 if has_review else 50
        if not has_review:
            issues.append("缺少审校环节,建议添加审校修订节点")

        # 维度 5: 格式规范 (描述覆盖率)
        described = sum(1 for n in graph.nodes.values() if n.description.strip())
        dims["formatting"] = min(100, 50 + (described / max(len(graph.nodes), 1)) * 50)

        score = sum(dims.values()) / len(dims) if dims else 0
        return Report(
            passed=score >= 70,
            score=score,
            dimensions=dims,
            issues=issues,
            suggestions=suggestions,
            evaluator="doc-5dim",
        )

    def build(self, graph: NodeGraph) -> Artifact:
        """DAG → 最终文档 (LLM 驱动)"""
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
            product_type=ProductType.DOC,
            content=content,
            files={"document.md": content},
            metadata={
                "generator": "goal-studio",
                "llm": self.llm.name,
                "llm_model": self.llm.model,
                "node_count": len(graph.nodes),
                "word_count": len(content),
                "format": "markdown",
            },
            graph_snapshot=graph.to_dict(),
        )

    def _build_build_prompt(self, graph: NodeGraph) -> str:
        parts = ["根据以下 DAG 生成完整文档:\n"]
        parts.append("节点:")
        for node in graph.nodes.values():
            slots_str = json.dumps(node.slots, ensure_ascii=False) if node.slots else "{}"
            parts.append(f"  - [{node.id}] {node.title} (L{node.layer.value}): {node.description} | slots={slots_str}")
        parts.append("\n边:")
        for e in graph.edges:
            parts.append(f"  - {e.source} → {e.target}")
        parts.append("\n请生成完整的 Markdown 文档。")
        return "\n".join(parts)

    def _fallback_build(self, graph: NodeGraph) -> str:
        """LLM 失败时的模板生成"""
        l1 = graph.get_layer(NodeLayer.L1_PLANNING)
        sections = ["# 生成文档\n", "Auto-generated by goal-studio.\n"]
        for node in l1:
            sections.append(f"\n## {node.title}\n")
            sections.append(node.description or "(待填充)")
            if node.slots:
                sections.append("\n**配置:**\n")
                for k, v in node.slots.items():
                    sections.append(f"- {k}: {v}")
        return "\n".join(sections)

    def export(self, artifact: Artifact, target: str) -> str:
        """导出文档"""
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
        """安装到文档目录"""
        doc_dir = env.get("doc_dir")
        if not doc_dir:
            return "doc_dir not provided in env"
        from pathlib import Path

        out = Path(doc_dir)
        out.mkdir(parents=True, exist_ok=True)
        for path, content in artifact.files.items():
            self._safe_write(out, path, content)
        return f"Installed to {out}"

    def get_schema(self) -> dict[str, Any]:
        return {
            "node_types": ["task", "agent", "tool", "decision"],
            "slots": {
                "task": ["title", "description", "expected_output"],
                "agent": ["role", "expertise", "responsibility"],
                "tool": ["sections", "word_count", "style", "citation_format"],
            },
        }


_ = Any
