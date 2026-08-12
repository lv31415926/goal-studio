"""NodeGraph: 三层 DAG 数据结构 + 拓扑校验 + 序列化

三层:
  L1 - 任务规划层: 高层任务分解,粗粒度
  L2 - Agent 分配层: 每个任务分配执行 Agent
  L3 - 节点精化层: 具体参数/指令/槽位
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeLayer(int, Enum):
    """三层画布"""

    L1_PLANNING = 1
    """任务规划层: 高层任务节点"""

    L2_ALLOCATION = 2
    """Agent 分配层: 执行 Agent 节点"""

    L3_REFINEMENT = 3
    """节点精化层: 参数/指令/槽位"""


class NodeStatus(str, Enum):
    """节点状态"""

    DRAFT = "draft"
    """AI 刚起草,待用户确认"""

    READY = "ready"
    """就绪,可执行"""

    RUNNING = "running"
    """执行中"""

    DONE = "done"
    """完成"""

    ERROR = "error"
    """出错"""

    BLOCKED = "blocked"
    """阻塞(缺口未填)"""


@dataclass
class Node:
    """DAG 节点"""

    id: str
    """唯一节点 ID"""

    layer: NodeLayer
    """所属层级 L1/L2/L3"""

    title: str
    """节点标题(2-5 词)"""

    description: str = ""
    """节点描述"""

    node_type: str = "task"
    """节点类型: task/agent/tool/decision/io"""

    status: NodeStatus = NodeStatus.DRAFT

    slots: dict[str, Any] = field(default_factory=dict)
    """节点槽位: 类型特定的参数键值对"""

    parent_id: str | None = None
    """父节点 ID(L2 的父是 L1, L3 的父是 L2)"""

    children_ids: list[str] = field(default_factory=list)
    """子节点 ID 列表"""

    position: tuple[float, float] = (0.0, 0.0)
    """画布坐标 (x, y)"""

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "layer": self.layer.value,
            "title": self.title,
            "description": self.description,
            "node_type": self.node_type,
            "status": self.status.value,
            "slots": self.slots,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "position": list(self.position),
            "metadata": self.metadata,
        }


@dataclass
class Edge:
    """DAG 边"""

    id: str
    source: str
    """源节点 ID"""

    target: str
    """目标节点 ID"""

    label: str = ""
    """边标签(可选)"""

    edge_type: str = "data"
    """边类型: data/control/condition"""

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "edge_type": self.edge_type,
            "metadata": self.metadata,
        }


@dataclass
class NodeGraph:
    """三层 DAG 图

    所有节点存在一个 flat 的 nodes dict,通过 layer 字段区分层级。
    parent_id / children_ids 维护层级关系,edges 维护同层执行顺序。
    """

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    root_id: str | None = None
    """根节点 ID(通常是 L1 的入口节点)"""

    version: int = 1
    """图版本,每次 refine 递增"""

    def add_node(self, node: Node) -> None:
        """添加节点"""
        if node.id in self.nodes:
            raise ValueError(f"Node {node.id} already exists")
        self.nodes[node.id] = node
        if node.parent_id and node.parent_id in self.nodes:
            self.nodes[node.parent_id].children_ids.append(node.id)

    def remove_node(self, node_id: str, cascade: bool = True) -> None:
        """移除节点,cascade=True 时级联移除子节点"""
        if node_id not in self.nodes:
            return
        node = self.nodes[node_id]
        # 级联子节点
        if cascade:
            for child_id in list(node.children_ids):
                self.remove_node(child_id, cascade=True)
        # 从父节点摘除
        if node.parent_id and node.parent_id in self.nodes:
            parent = self.nodes[node.parent_id]
            parent.children_ids = [c for c in parent.children_ids if c != node_id]
        # 移除相关边
        self.edges = [
            e for e in self.edges if e.source != node_id and e.target != node_id
        ]
        del self.nodes[node_id]

    def add_edge(self, edge: Edge) -> None:
        """添加边,自动做拓扑校验"""
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise ValueError("Edge endpoints must exist")
        if self._would_create_cycle(edge.source, edge.target):
            raise ValueError(
                f"Edge {edge.source} -> {edge.target} would create a cycle"
            )
        self.edges.append(edge)

    def _would_create_cycle(self, source: str, target: str) -> bool:
        """DFS 检测加 source->target 边后是否会成环"""
        if source == target:
            return True
        visited: set[str] = set()
        stack = [target]
        while stack:
            cur = stack.pop()
            if cur == source:
                return True
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(
                e.target for e in self.edges if e.source == cur
            )
        return False

    def topological_order(self, layer: NodeLayer | None = None) -> list[str]:
        """返回拓扑排序,可按层过滤"""
        nodes = (
            [nid for nid, n in self.nodes.items() if n.layer == layer]
            if layer
            else list(self.nodes.keys())
        )
        in_degree = {nid: 0 for nid in nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in nodes}
        for e in self.edges:
            if e.source in in_degree and e.target in in_degree:
                adj[e.source].append(e.target)
                in_degree[e.target] += 1
        queue = [nid for nid, d in in_degree.items() if d == 0]
        order: list[str] = []
        while queue:
            cur = queue.pop(0)
            order.append(cur)
            for nxt in adj[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(nodes):
            raise ValueError("Graph has a cycle")
        return order

    def get_layer(self, layer: NodeLayer) -> list[Node]:
        """获取指定层的所有节点"""
        return [n for n in self.nodes.values() if n.layer == layer]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "root_id": self.root_id,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeGraph:
        g = cls(root_id=data.get("root_id"), version=data.get("version", 1))
        for nid, nd in data.get("nodes", {}).items():
            n = Node(
                id=nd["id"],
                layer=NodeLayer(nd["layer"]),
                title=nd["title"],
                description=nd.get("description", ""),
                node_type=nd.get("node_type", "task"),
                status=NodeStatus(nd.get("status", "draft")),
                slots=nd.get("slots", {}),
                parent_id=nd.get("parent_id"),
                children_ids=nd.get("children_ids", []),
                position=tuple(nd.get("position", [0.0, 0.0])),
                metadata=nd.get("metadata", {}),
            )
            g.nodes[n.id] = n
        for ed in data.get("edges", []):
            g.edges.append(
                Edge(
                    id=ed["id"],
                    source=ed["source"],
                    target=ed["target"],
                    label=ed.get("label", ""),
                    edge_type=ed.get("edge_type", "data"),
                    metadata=ed.get("metadata", {}),
                )
            )
        return g
