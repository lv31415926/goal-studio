"""CodeAdapter: 代码项目适配器 (LLM 驱动)

把目标转化为可运行的代码项目骨架。
- draft: LLM 生成三层 DAG (需求→架构→实现→测试→部署)
- refine: 应用精化补丁到图
- validate: 代码质量评估 (结构/可维护性/测试覆盖/文档/安全)
- build: LLM 根据图生成项目骨架代码
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import (
    Artifact, GoalContract, Patch, PatchOp, ProductType, Report,
)
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer, NodeStatus
from goal_studio.llm import LLMMessage, get_llm
from goal_studio.llm.base import BaseLLMProvider


DRAFT_SYSTEM_PROMPT = """你是 goal-studio 的代码项目架构助手。你的任务是把用户目标分解为三层 DAG 工作流,用于生成代码项目骨架。

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
1. L1 任务规划层: 3-7 个节点,覆盖 需求分析→架构设计→核心实现→测试编写→部署配置,parent_id=null
2. L2 Agent 分配层: 每个 L1 节点下 1-2 个 L2 子节点,node_type=agent,描述执行该任务的角色(如架构师/开发工程师/测试工程师)
3. L3 节点精化层: 关键 L2 节点下 1-2 个 L3 子节点,node_type=tool,slots 必填具体参数(如文件路径/模块名/技术栈/测试框架)
4. 节点标题简洁(2-5词),描述具体可执行
5. edges 只连同层节点的执行顺序,无环;父子关系通过 parent_id 表达
6. 根据 goal 的四要素和约束定制每个节点
7. 只输出 JSON,不要其他文字"""

BUILD_SYSTEM_PROMPT = """你是 goal-studio 的代码生成助手。根据 DAG 工作流生成项目骨架文件。

输出严格的 JSON 格式:
{
  "files": {
    "README.md": "项目说明文档内容",
    "src/main.py": "主入口文件代码",
    "tests/test_main.py": "测试文件代码"
  },
  "entry_point": "src/main.py",
  "dependencies": ["依赖包1", "依赖包2"]
}

要求:
1. 根据 L1 节点生成对应的模块文件
2. 根据 L3 节点的 slots 中的技术栈/框架信息生成具体代码
3. 文件路径使用标准项目结构
4. 代码包含基本骨架和注释,可运行
5. 只输出 JSON,不要其他文字"""


class CodeAdapter(BaseAdapter):
    """代码项目适配器 (LLM 驱动)"""

    product_type = "code"
    name = "Code Adapter"
    description = "把目标转化为可运行的代码项目骨架 (LLM 驱动)"

    def __init__(self, llm: BaseLLMProvider | None = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> BaseLLMProvider:
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def draft(self, goal: GoalContract) -> NodeGraph:
        """目标 → 三层 DAG (代码项目)"""
        user_prompt = self._build_draft_prompt(goal)
        messages = [
            LLMMessage(role="system", content=DRAFT_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]
        try:
            data = self.llm.complete_json(messages, temperature=0.7, max_tokens=16384)
            graph = self._parse_dag_response(data)
            # 空响应保护: LLM 返回空 JSON 时降级到模板 (推理模型 max_tokens 不足时发生)
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
        parts.append("\n请生成代码项目的三层 DAG: L1 任务规划(3-7 节点) + L2 Agent 分配 + L3 节点精化(带技术栈/文件路径 slots)。")
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
        LAYER_GAP = 180
        L1_GAP = 280
        CHILD_GAP = 180

        l1_nodes = [n for n in g.nodes.values() if n.layer == NodeLayer.L1_PLANNING]
        try:
            l1_order = g.topological_order(NodeLayer.L1_PLANNING)
            l1_nodes = [g.nodes[nid] for nid in l1_order if nid in g.nodes]
        except ValueError:
            pass

        for i, node in enumerate(l1_nodes):
            node.position = (i * L1_GAP, 0.0)

        for l1 in l1_nodes:
            l2_children = [
                g.nodes[cid] for cid in l1.children_ids
                if cid in g.nodes and g.nodes[cid].layer == NodeLayer.L2_ALLOCATION
            ]
            if not l2_children:
                continue
            total_w = (len(l2_children) - 1) * CHILD_GAP
            start_x = l1.position[0] - total_w / 2
            for j, child in enumerate(l2_children):
                child.position = (start_x + j * CHILD_GAP, LAYER_GAP)
                l3_children = [
                    g.nodes[cid] for cid in child.children_ids
                    if cid in g.nodes and g.nodes[cid].layer == NodeLayer.L3_REFINEMENT
                ]
                if not l3_children:
                    continue
                l3_total_w = (len(l3_children) - 1) * (CHILD_GAP * 0.7)
                l3_start_x = child.position[0] - l3_total_w / 2
                for k, l3 in enumerate(l3_children):
                    l3.position = (l3_start_x + k * (CHILD_GAP * 0.7), LAYER_GAP * 2)

    def _fallback_draft(self, goal: GoalContract, error: str = "") -> NodeGraph:
        """LLM 失败时的三层模板降级 (代码项目)"""
        g = NodeGraph()

        l1_tasks = [
            ("l1-req", "需求分析", f"解析目标「{goal.goal[:50]}」,提取功能需求和非功能需求"),
            ("l1-arch", "架构设计", "设计项目结构、模块划分、技术栈选型"),
            ("l1-impl", "核心实现", "实现核心业务逻辑和数据流"),
            ("l1-test", "测试编写", "编写单元测试和集成测试"),
            ("l1-deploy", "部署配置", "配置构建、部署和运行环境"),
        ]
        prev: str | None = None
        for tid, title, desc in l1_tasks:
            n = Node(id=tid, layer=NodeLayer.L1_PLANNING, title=title, description=desc)
            g.add_node(n)
            if prev:
                g.add_edge(Edge(id=f"e-{prev}-{n.id}", source=prev, target=n.id))
            prev = tid
        g.root_id = "l1-req"

        l2_specs = [
            ("l2-req-analyst", "l1-req", "需求分析师", "提取用户故事、验收标准、非功能需求"),
            ("l2-arch-architect", "l1-arch", "架构师", "设计目录结构、模块依赖、技术栈"),
            ("l2-impl-dev", "l1-impl", "开发工程师", "实现核心模块代码和接口"),
            ("l2-test-qa", "l1-test", "测试工程师", "编写测试用例,设计测试策略"),
            ("l2-deploy-devops", "l1-deploy", "DevOps工程师", "配置 CI/CD、Docker、部署脚本"),
        ]
        for tid, parent, title, desc in l2_specs:
            n = Node(id=tid, layer=NodeLayer.L2_ALLOCATION, title=title,
                     description=desc, node_type="agent", parent_id=parent)
            g.add_node(n)

        l3_specs: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
            ("l3-arch-stack", "l2-arch-architect", "技术栈选型器", "选择技术栈和目录结构", "tool",
             {"language": "python", "framework": "fastapi", "db": "sqlite", "structure": "src/tests/docs"}),
            ("l3-impl-skeleton", "l2-impl-dev", "代码骨架生成", "生成代码骨架和模块结构", "tool",
             {"entry": "src/main.py", "modules": ["api", "core", "models"], "pattern": "mvc"}),
            ("l3-test-framework", "l2-test-qa", "测试框架配置", "配置测试框架和覆盖目标", "tool",
             {"framework": "pytest", "coverage_target": 80, "test_dirs": ["tests/unit", "tests/integration"]}),
            ("l3-deploy-docker", "l2-deploy-devops", "Docker配置", "配置 Docker 镜像、端口和环境变量", "tool",
             {"base_image": "python:3.11-slim", "port": 8000, "env": [".env.example"]}),
        ]
        for tid, parent, title, desc, ntype, slots in l3_specs:
            n = Node(id=tid, layer=NodeLayer.L3_REFINEMENT, title=title,
                     description=desc, node_type=ntype, parent_id=parent, slots=slots)
            g.add_node(n)

        self._apply_hierarchical_layout(g)
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
                    Edge(id=f"e-{patch.target_node_id}-{patch.target_node_id_2}",
                         source=patch.target_node_id, target=patch.target_node_id_2)
                )
        return new_graph

    def validate(self, graph: NodeGraph) -> Report:
        """代码项目质量评估 (5 维)"""
        dims: dict[str, float] = {}
        issues: list[str] = []
        suggestions: list[str] = []

        l1_nodes = graph.get_layer(NodeLayer.L1_PLANNING)
        if len(l1_nodes) < 3:
            issues.append("L1 任务节点过少,建议至少 3 个")
            dims["architecture_clarity"] = 45
        elif len(l1_nodes) > 8:
            issues.append("L1 任务节点过多,建议合并")
            dims["architecture_clarity"] = 65
        else:
            dims["architecture_clarity"] = 85

        has_stack = any(
            "language" in n.slots or "framework" in n.slots or "modules" in n.slots
            for n in graph.nodes.values()
        )
        dims["modularity"] = 85 if has_stack else 55
        if not has_stack:
            suggestions.append("建议在 L3 节点补充技术栈/模块信息")

        has_test = any(
            "test" in n.title.lower() or "测试" in n.title or "pytest" in str(n.slots)
            for n in graph.nodes.values()
        )
        dims["test_coverage"] = 82 if has_test else 50
        if not has_test:
            issues.append("缺少测试节点,建议添加测试编写环节")
            suggestions.append("建议增加测试框架配置 L3 节点")

        has_deploy = any(
            "deploy" in n.title.lower() or "部署" in n.title or "docker" in str(n.slots).lower()
            for n in graph.nodes.values()
        )
        dims["deploy_readiness"] = 80 if has_deploy else 50
        if not has_deploy:
            suggestions.append("建议增加部署配置节点")

        described = sum(1 for n in graph.nodes.values() if n.description.strip())
        dims["documentation"] = min(100, 50 + (described / max(len(graph.nodes), 1)) * 50)

        score = sum(dims.values()) / len(dims) if dims else 0
        return Report(
            passed=score >= 70,
            score=score,
            dimensions=dims,
            issues=issues,
            suggestions=suggestions,
            evaluator="code-5dim",
        )

    def build(self, graph: NodeGraph) -> Artifact:
        """DAG → 项目骨架代码 (LLM 驱动)"""
        user_prompt = self._build_build_prompt(graph)
        messages = [
            LLMMessage(role="system", content=BUILD_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]

        try:
            data = self.llm.complete_json(messages, temperature=0.5, max_tokens=16384)
            files = data.get("files", {})
            # 修复推理模型双转义导致的字面 backslash-n 问题
            files = {p: BaseAdapter._normalize_content(c) for p, c in files.items()}
            entry_point = data.get("entry_point", "")
            dependencies = data.get("dependencies", [])
            content = files.get("README.md", "# Generated Project\n")
        except Exception:
            files = self._fallback_build(graph)
            content = files.get("README.md", "# Generated Project\n")
            entry_point = "src/main.py"
            dependencies = []

        return Artifact(
            product_type=ProductType.CODE,
            content=content,
            files=files,
            metadata={
                "generator": "goal-studio",
                "llm": self.llm.name,
                "llm_model": self.llm.model,
                "node_count": len(graph.nodes),
                "entry_point": entry_point,
                "dependencies": dependencies,
            },
            graph_snapshot=graph.to_dict(),
        )

    def _build_build_prompt(self, graph: NodeGraph) -> str:
        parts = ["根据以下 DAG 生成代码项目骨架:\n"]
        parts.append("节点:")
        for node in graph.nodes.values():
            slots_str = json.dumps(node.slots, ensure_ascii=False) if node.slots else "{}"
            parts.append(f"  - [{node.id}] {node.title} (L{node.layer.value}): {node.description} | slots={slots_str}")
        parts.append("\n边:")
        for e in graph.edges:
            parts.append(f"  - {e.source} → {e.target}")
        parts.append("\n请生成完整的项目骨架文件 (JSON 格式)。")
        return "\n".join(parts)

    def _fallback_build(self, graph: NodeGraph) -> dict[str, str]:
        """LLM 失败时的模板生成"""
        l1 = graph.get_layer(NodeLayer.L1_PLANNING)
        readme = ["# Generated Project\n", "Auto-generated by goal-studio.\n", "## 模块\n"]
        for node in l1:
            readme.append(f"- **{node.title}**: {node.description}")
        stack_info = ""
        for n in graph.nodes.values():
            if n.layer == NodeLayer.L3_REFINEMENT and ("language" in n.slots or "framework" in n.slots):
                stack_info = f"\n技术栈: {n.slots.get('language', 'python')} / {n.slots.get('framework', 'fastapi')}"
                break
        readme.append(stack_info)

        main_code = '"""\n项目主入口\nAuto-generated by goal-studio\n"""\n\n\ndef main() -> None:\n    """主函数"""\n    print("Hello from goal-studio generated project")\n\n\nif __name__ == "__main__":\n    main()\n'

        test_code = '"""\n测试文件\nAuto-generated by goal-studio\n"""\nimport pytest\nfrom src.main import main\n\n\ndef test_main() -> None:\n    """测试主函数"""\n    pass\n'

        return {
            "README.md": "\n".join(readme),
            "src/main.py": main_code,
            "tests/test_main.py": test_code,
            "requirements.txt": "# 项目依赖\n# fastapi\n# pytest\n",
        }

    def export(self, artifact: Artifact, target: str) -> str:
        """导出代码项目"""
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
        """安装到目标目录"""
        project_dir = env.get("project_dir")
        if not project_dir:
            return "project_dir not provided in env"
        from pathlib import Path

        out = Path(project_dir)
        out.mkdir(parents=True, exist_ok=True)
        for path, content in artifact.files.items():
            self._safe_write(out, path, content)
        return f"Installed to {out}"

    def get_schema(self) -> dict[str, Any]:
        return {
            "node_types": ["task", "agent", "tool", "decision"],
            "slots": {
                "task": ["title", "description", "expected_output"],
                "agent": ["role", "tech_stack", "responsibility"],
                "tool": ["file_path", "language", "framework", "modules"],
            },
        }


_ = Any
