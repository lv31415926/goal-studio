"""Layer 5: 动作执行器

把 ParsedIntent 转为 DAG mutation 操作 (Patch)。
支持 undo/redo 栈,拓扑校验前置。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goal_studio.core.contract import Patch, PatchOp
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer, NodeStatus
from goal_studio.intent_parser.llm_parser import ParsedIntent


@dataclass
class ExecutionResult:
    """执行结果"""
    ok: bool
    patch: Patch | None = None
    new_graph: NodeGraph | None = None
    reason: str = ""
    undo_available: bool = False
    needs_confirmation: bool = False
    """边界场景: 破坏性操作需前端二次确认(delete 级联 / 破坏性 connect)"""
    confirmation_context: dict[str, Any] = field(default_factory=dict)
    """确认上下文: {cascade_nodes: [...], cycle_path: [...], ...}"""
    cycle_path: list[str] | None = None
    """边界场景: 循环依赖 — 成环节点路径,供提示"断开哪条边" """
    conflict_warning: str = ""
    """边界场景: 多模态冲突 — 鼠标悬停A但语音说B时的告警"""
    executed_graph_snapshot: NodeGraph | None = None
    """边界场景: ASR延迟 — 执行前的图快照(锁定说话开始时刻的画布状态)"""



class ActionExecutor:
    """Layer 5: 动作执行器"""

    def __init__(self) -> None:
        self._undo_stack: list[tuple[NodeGraph, Patch]] = []
        self._redo_stack: list[tuple[NodeGraph, Patch]] = []
        self._max_history = 50

    def execute(
        self,
        intent: ParsedIntent,
        graph: NodeGraph,
        resolved_node_id: str | None = None,
        force: bool = False,
        conflict_hover_id: str | None = None,
    ) -> ExecutionResult:
        """执行意图

        Args:
            intent: 解析后的意图
            graph: 当前图
            resolved_node_id: Layer 4 解析出的目标节点 ID
            force: 确认后强制执行(跳过级联确认门控)
            conflict_hover_id: 当前鼠标悬停节点(边界场景: 多模态冲突检测)

        Returns:
            ExecutionResult
        """
        if not intent.intent:
            return ExecutionResult(ok=False, reason="意图为空")

        # R2-11: query 意图直接返回,跳过 graph_snapshot 深拷贝(无 DAG 修改)
        if intent.intent == "query":
            return ExecutionResult(
                ok=True,
                reason=f"查询: {intent.raw_text}",
                undo_available=bool(self._undo_stack),
            )

        # 边界场景: ASR延迟 — 执行前深拷贝当前图作为快照(锁定执行时刻画布状态)
        # R2-11: 仅非 query 意图才做深拷贝
        graph_snapshot = NodeGraph.from_dict(graph.to_dict())

        # 边界场景: 多模态冲突 — 鼠标悬停A但解析到B,语音优先但告警
        conflict_warning = ""
        if (
            conflict_hover_id
            and resolved_node_id
            and conflict_hover_id != resolved_node_id
        ):
            hover_title = (
                graph.nodes[conflict_hover_id].title
                if conflict_hover_id in graph.nodes
                else conflict_hover_id
            )
            target_title = (
                graph.nodes[resolved_node_id].title
                if resolved_node_id in graph.nodes
                else resolved_node_id
            )
            conflict_warning = (
                f"多模态冲突: 鼠标悬停「{hover_title}」但指令指向「{target_title}」,"
                "已按指令执行(语音优先)"
            )

        if intent.intent == "modify":
            result = self._execute_modify(intent, graph, resolved_node_id)
        elif intent.intent == "create":
            result = self._execute_create(intent, graph, resolved_node_id)
        elif intent.intent == "delete":
            result = self._execute_delete(intent, graph, resolved_node_id, force=force)
        elif intent.intent == "connect":
            result = self._execute_connect(intent, graph, resolved_node_id)
        else:
            return ExecutionResult(ok=False, reason=f"未知意图类型: {intent.intent}")

        # 附带执行前图快照(边界场景: ASR延迟 — 锁定执行时刻画布状态)
        result.executed_graph_snapshot = graph_snapshot
        # 附带冲突告警(不阻断执行)
        if conflict_warning and result.ok:
            result.conflict_warning = conflict_warning
        return result

    def _execute_modify(
        self,
        intent: ParsedIntent,
        graph: NodeGraph,
        node_id: str | None,
    ) -> ExecutionResult:
        """执行修改"""
        if not node_id:
            return ExecutionResult(ok=False, reason="未指定目标节点")
        if node_id not in graph.nodes:
            return ExecutionResult(ok=False, reason=f"节点 {node_id} 不存在")

        patch = Patch(
            op=PatchOp.UPDATE_NODE,
            target_node_id=node_id,
            slots=intent.slots,
            source="voice" if intent.raw_text else "text",
            rationale=intent.raw_text[:100],
        )
        new_graph = self._apply_patch(graph, patch)
        self._push_undo(graph, patch)
        return ExecutionResult(
            ok=True,
            patch=patch,
            new_graph=new_graph,
            undo_available=True,
        )

    def _execute_create(
        self,
        intent: ParsedIntent,
        graph: NodeGraph,
        parent_id: str | None,
    ) -> ExecutionResult:
        """执行创建"""
        new_node_data = intent.slots or {"title": "新节点"}
        patch = Patch(
            op=PatchOp.ADD_NODE,
            target_node_id=parent_id,
            new_node={
                "title": new_node_data.get("title", "新节点"),
                "description": new_node_data.get("description", ""),
                "layer": new_node_data.get("layer", 2),
                "slots": {
                    k: v for k, v in new_node_data.items()
                    if k not in ("title", "description", "layer")
                },
            },
            source="voice" if intent.raw_text else "text",
        )
        new_graph = self._apply_patch(graph, patch)
        self._push_undo(graph, patch)
        return ExecutionResult(ok=True, patch=patch, new_graph=new_graph, undo_available=True)

    def _execute_delete(
        self,
        intent: ParsedIntent,
        graph: NodeGraph,
        node_id: str | None,
        force: bool = False,
    ) -> ExecutionResult:
        """执行删除

        边界场景: 删除级联 — 有级联节点时返回 needs_confirmation=True + cascade_nodes,
        前端确认后带 force=True 重新调用执行。
        """
        if not node_id or node_id not in graph.nodes:
            return ExecutionResult(ok=False, reason="未指定有效目标节点")

        node = graph.nodes[node_id]
        cascade_ids = graph.get_descendants(node_id)
        # 级联节点标题列表供前端展示
        cascade_nodes = [
            {"id": cid, "title": graph.nodes[cid].title}
            for cid in cascade_ids if cid in graph.nodes
        ]

        # 有级联且未确认 → 返回确认请求(不实际删除)
        if cascade_nodes and not force:
            return ExecutionResult(
                ok=False,
                needs_confirmation=True,
                reason=f"删除「{node.title}」将级联删除 {len(cascade_nodes)} 个下游节点",
                confirmation_context={
                    "op": "delete",
                    "target_node_id": node_id,
                    "target_title": node.title,
                    "cascade_nodes": cascade_nodes,
                },
            )

        patch = Patch(
            op=PatchOp.REMOVE_NODE,
            target_node_id=node_id,
            source="voice" if intent.raw_text else "text",
            rationale=f"删除 {node.title}"
            + (f"(级联 {len(cascade_nodes)} 子节点)" if cascade_nodes else ""),
        )
        new_graph = self._apply_patch(graph, patch)
        self._push_undo(graph, patch)
        return ExecutionResult(
            ok=True,
            patch=patch,
            new_graph=new_graph,
            undo_available=True,
            reason=f"已删除 {node.title}"
            + (f" 及 {len(cascade_nodes)} 个子节点" if cascade_nodes else ""),
        )

    def _execute_connect(
        self,
        intent: ParsedIntent,
        graph: NodeGraph,
        source_id: str | None,
    ) -> ExecutionResult:
        """执行连接"""
        # connect 需要 source 和 target,从 slots 提取 target
        # R2-13: 强制 str 转换,避免 slots 中存入非 str 类型时后续比较失败
        target_id = str(intent.slots.get("target_node_id", ""))
        if not source_id or not target_id:
            return ExecutionResult(ok=False, reason="连接需要源节点和目标节点")
        if source_id not in graph.nodes or target_id not in graph.nodes:
            return ExecutionResult(ok=False, reason="节点不存在")

        # 拓扑校验前置(边界场景: 循环依赖断边提示)
        cycle = graph._find_cycle_path(source_id, target_id)
        if cycle is not None:
            return ExecutionResult(
                ok=False,
                reason=f"连接 {source_id} → {target_id} 会形成环",
                needs_confirmation=True,
                cycle_path=cycle,
                confirmation_context={
                    "op": "connect",
                    "cycle_path": cycle,
                    "cycle_edges": [
                        [cycle[i], cycle[i + 1]] for i in range(len(cycle) - 1)
                    ],
                },
            )

        patch = Patch(
            op=PatchOp.ADD_EDGE,
            target_node_id=source_id,
            target_node_id_2=target_id,
            source="voice" if intent.raw_text else "text",
        )
        new_graph = self._apply_patch(graph, patch)
        self._push_undo(graph, patch)
        return ExecutionResult(ok=True, patch=patch, new_graph=new_graph, undo_available=True)

    def _apply_patch(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        """应用补丁生成新图"""
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
            import uuid
            # R2-12: 从 patch.new_node 读取 node_type 和 status
            node_type = patch.new_node.get("node_type", "task")
            status_str = patch.new_node.get("status", "draft")
            status = NodeStatus(status_str) if status_str else NodeStatus.DRAFT
            node = Node(
                id=patch.new_node.get("id", f"n-{uuid.uuid4().hex[:8]}"),
                layer=NodeLayer(patch.new_node.get("layer", 2)),
                title=patch.new_node.get("title", "New Node"),
                description=patch.new_node.get("description", ""),
                node_type=node_type,
                status=status,
                slots=patch.new_node.get("slots", {}),
                parent_id=patch.target_node_id,
            )
            new_graph.add_node(node)

        elif patch.op == PatchOp.REMOVE_NODE and patch.target_node_id:
            new_graph.remove_node(patch.target_node_id)

        elif patch.op == PatchOp.ADD_EDGE and patch.target_node_id and patch.target_node_id_2:
            # PR-ROUND-2/C-08: edge id 纳入 edge_type,避免同 source-target
            # 不同 edge_type(如 data/control)时 id 冲突导致 add_edge 去重误跳过
            edge_type = patch.slots.get("edge_type", "data")
            new_graph.add_edge(
                Edge(
                    id=f"e-{patch.target_node_id}-{patch.target_node_id_2}-{edge_type}",
                    source=patch.target_node_id,
                    target=patch.target_node_id_2,
                    edge_type=edge_type,
                )
            )

        return new_graph

    def _would_cycle(self, graph: NodeGraph, source: str, target: str) -> bool:
        """检测加 source→target 边后是否成环"""
        return graph._would_create_cycle(source, target)

    def _push_undo(self, graph: NodeGraph, patch: Patch) -> None:
        """压入 undo 栈"""
        self._undo_stack.append((NodeGraph.from_dict(graph.to_dict()), patch))
        self._redo_stack.clear()
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)

    def undo(self, current_graph: NodeGraph) -> ExecutionResult | None:
        """撤销"""
        if not self._undo_stack:
            return None
        prev_graph, patch = self._undo_stack.pop()
        self._redo_stack.append((current_graph, patch))
        return ExecutionResult(
            ok=True,
            new_graph=prev_graph,
            reason=f"撤销: {patch.op.value}",
            undo_available=bool(self._undo_stack),
        )

    def redo(self, current_graph: NodeGraph) -> ExecutionResult | None:
        """重做"""
        if not self._redo_stack:
            return None
        next_graph, patch = self._redo_stack.pop()
        self._undo_stack.append((current_graph, patch))
        return ExecutionResult(
            ok=True,
            new_graph=next_graph,
            reason=f"重做: {patch.op.value}",
            undo_available=True,
        )


_ = Any
