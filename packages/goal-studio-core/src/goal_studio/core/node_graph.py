"""NodeGraph: 三层 DAG 数据结构 + 拓扑校验 + 序列化

三层:
  L1 - 任务规划层: 高层任务分解,粗粒度
  L2 - Agent 分配层: 每个任务分配执行 Agent
  L3 - 节点精化层: 具体参数/指令/槽位
"""
from __future__ import annotations

import collections
import threading
import warnings
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

    # PR-Q: 邻接表索引 — add_edge/remove_node/from_dict 时维护,
    # 避免 _find_cycle_path/get_descendants 全量扫描
    _adj_out: dict[str, list[str]] = field(default_factory=dict, repr=False, compare=False)
    _adj_in: dict[str, list[str]] = field(default_factory=dict, repr=False, compare=False)
    # R2-04: 边端点三元组索引 — O(1) 去重检查
    _edge_endpoints: set[tuple[str, str, str]] = field(
        default_factory=set, repr=False, compare=False
    )
    # PR-M: 线程安全锁,保护并发图操作(RLock 可重入,remove_node 递归调用无死锁)
    # 只读方法(get_descendants/_find_cycle_path/validate/to_dict/topological_order)不加锁,
    # 依赖调用方在 per-session 锁内完成读取
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def _build_adjacency(self) -> None:
        """从 edges 重建邻接表索引"""
        self._adj_out.clear()
        self._adj_in.clear()
        # R2-04: 同步重建 _edge_endpoints 索引
        self._edge_endpoints.clear()
        for e in self.edges:
            self._adj_out.setdefault(e.source, []).append(e.target)
            self._adj_in.setdefault(e.target, []).append(e.source)
            self._edge_endpoints.add((e.source, e.target, e.edge_type))

    def add_node(self, node: Node) -> None:
        """添加节点"""
        with self._lock:
            if node.id in self.nodes:
                raise ValueError(f"Node {node.id} already exists")
            self.nodes[node.id] = node
            if node.parent_id and node.parent_id in self.nodes:
                self.nodes[node.parent_id].children_ids.append(node.id)

    def remove_node(self, node_id: str, cascade: bool = True) -> None:
        """移除节点,cascade=True 时级联移除子节点"""
        with self._lock:
            if node_id not in self.nodes:
                return
            node = self.nodes[node_id]
            # 级联子节点(RLock 可重入,递归调用无死锁)
            if cascade:
                for child_id in list(node.children_ids):
                    self.remove_node(child_id, cascade=True)
                # R2-01: 同时级联 _adj_out 边下游节点(与 get_descendants 范围对齐)
                # 注意避免重复删除已在 children_ids 中的节点
                for downstream_id in list(self._adj_out.get(node_id, [])):
                    if downstream_id in self.nodes:
                        self.remove_node(downstream_id, cascade=True)
            # 从父节点摘除
            if node.parent_id and node.parent_id in self.nodes:
                parent = self.nodes[node.parent_id]
                parent.children_ids = [c for c in parent.children_ids if c != node_id]
            # 移除相关边
            self.edges = [
                e for e in self.edges if e.source != node_id and e.target != node_id
            ]
            # R2-04: 同步移除 _edge_endpoints 中相关条目
            self._edge_endpoints = {
                (s, t, et) for (s, t, et) in self._edge_endpoints
                if s != node_id and t != node_id
            }
            # PR-Q: 同步更新邻接表
            self._adj_out.pop(node_id, None)
            self._adj_in.pop(node_id, None)
            # R2-05: 反向定位清理邻接表引用(O(度数) 替代全量扫描)
            # 用 _adj_in[node_id] 找到指向 node_id 的源节点,清理其 _adj_out
            for source in self._adj_in.get(node_id, []):
                out_list = self._adj_out.get(source)
                if out_list:
                    while node_id in out_list:
                        out_list.remove(node_id)
                    if not out_list:
                        self._adj_out.pop(source, None)
            # 用 _adj_out[node_id] 找到 node_id 指向的目标节点,清理其 _adj_in
            for target in self._adj_out.get(node_id, []):
                in_list = self._adj_in.get(target)
                if in_list:
                    while node_id in in_list:
                        in_list.remove(node_id)
                    if not in_list:
                        self._adj_in.pop(target, None)
            del self.nodes[node_id]

    def add_edge(self, edge: Edge) -> None:
        """添加边,自动做拓扑校验 + 去重(P-LOW-2-2)

        去重规则:
          - 同 id 边: 跳过(undo→redo→undo 不产生重复)
          - 同 (source, target, edge_type) 三元组: 跳过
        """
        with self._lock:
            if edge.source not in self.nodes or edge.target not in self.nodes:
                raise ValueError("Edge endpoints must exist")
            if self._would_create_cycle(edge.source, edge.target):
                raise ValueError(
                    f"Edge {edge.source} -> {edge.target} would create a cycle"
                )
            # P-LOW-2-2 + R2-04: 去重 — 同 id 或同 (source, target, edge_type) 跳过(O(1) set 查询)
            for existing in self.edges:
                if existing.id == edge.id:
                    return
            if (edge.source, edge.target, edge.edge_type) in self._edge_endpoints:
                return
            self.edges.append(edge)
            self._edge_endpoints.add((edge.source, edge.target, edge.edge_type))
            # PR-Q: 维护邻接表索引
            self._adj_out.setdefault(edge.source, []).append(edge.target)
            self._adj_in.setdefault(edge.target, []).append(edge.source)

    def remove_edge(self, edge_id: str) -> bool:
        """移除边,返回是否成功移除"""
        with self._lock:
            for i, e in enumerate(self.edges):
                if e.id == edge_id:
                    self.edges.pop(i)
                    # R2-04: 同步移除 _edge_endpoints 索引
                    self._edge_endpoints.discard((e.source, e.target, e.edge_type))
                    # 同步清理邻接表
                    out_list = self._adj_out.get(e.source)
                    if out_list and e.target in out_list:
                        out_list.remove(e.target)
                        if not out_list:
                            self._adj_out.pop(e.source, None)
                    in_list = self._adj_in.get(e.target)
                    if in_list and e.source in in_list:
                        in_list.remove(e.source)
                        if not in_list:
                            self._adj_in.pop(e.target, None)
                    return True
            return False

    def _would_create_cycle(self, source: str, target: str) -> bool:
        """DFS 检测加 source->target 边后是否会成环"""
        return self._find_cycle_path(source, target) is not None

    def _find_cycle_path(self, source: str, target: str) -> list[str] | None:
        """检测加 source->target 边后是否成环,返回成环路径(含 source...target->source)

        边界场景: 循环依赖断边提示 — 冲突时告诉用户"断开哪条边"。
        返回 None 表示无环;返回 [source, ..., target] 表示 target 经路径回到 source。
        """
        if source == target:
            # R2-02: 自环返回 [source, source],使 cycle_edges 能正确构造
            return [source, source]
        # DFS 从 target 出发,看能否回到 source
        parent: dict[str, str | None] = {target: None}
        stack = [target]
        while stack:
            cur = stack.pop()
            if cur == source:
                # parent 链: parent[discovered] = discoverer, discoverer 更靠近 target(起点)。
                # 从 source 沿 parent 链回溯到 target,得到 [source, ..., target]
                rev_path: list[str] = []
                n: str | None = source
                while n is not None:
                    rev_path.append(n)
                    n = parent.get(n)
                # rev_path = [source, ..., target]
                # 环路径方向: target → ... → source → target (加入 source→target 边闭合)
                return rev_path[::-1] + [target]
            # PR-Q: 使用邻接表索引替代全量扫描 self.edges
            for next_node in self._adj_out.get(cur, []):
                if next_node in parent:
                    continue
                parent[next_node] = cur
                stack.append(next_node)
        return None

    def topological_order(self, layer: NodeLayer | None = None) -> list[str]:
        """返回拓扑排序,可按层过滤"""
        nodes = (
            [nid for nid, n in self.nodes.items() if n.layer == layer]
            if layer
            else list(self.nodes.keys())
        )
        in_degree = dict.fromkeys(nodes, 0)
        adj: dict[str, list[str]] = {nid: [] for nid in nodes}
        # R2-03: 复用 _adj_out 索引(按 layer 过滤),避免重新扫描 self.edges
        for src, targets in self._adj_out.items():
            if src not in in_degree:
                continue
            for tgt in targets:
                if tgt in in_degree:
                    adj[src].append(tgt)
                    in_degree[tgt] += 1
        # R2-03: 用 collections.deque + popleft() 替代 list.pop(0) (O(1) vs O(n))
        queue = collections.deque(nid for nid, d in in_degree.items() if d == 0)
        order: list[str] = []
        while queue:
            cur = queue.popleft()
            order.append(cur)
            for nxt in adj[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(nodes):
            raise ValueError("Graph has a cycle")
        return order

    def validate(self) -> None:
        """校验图完整性(专家 3 建议,from_dict 后调用)

        检查项:
          1. 边的端点必须存在于 nodes
          2. 无环(topological_order 会抛 ValueError)
          3. parent_id 引用的节点必须存在
          4. children_ids 中的节点必须存在且 parent_id 一致
        """
        # 1. 边端点存在性
        for e in self.edges:
            if e.source not in self.nodes:
                raise ValueError(f"Edge {e.id} source {e.source} not in nodes")
            if e.target not in self.nodes:
                raise ValueError(f"Edge {e.id} target {e.target} not in nodes")
        # 2. 无环检测
        self.topological_order()
        # 3. parent_id 一致性
        for nid, n in self.nodes.items():
            if n.parent_id is not None and n.parent_id not in self.nodes:
                raise ValueError(
                    f"Node {nid} parent_id {n.parent_id} not in nodes"
                )
            for cid in n.children_ids:
                if cid not in self.nodes:
                    raise ValueError(
                        f"Node {nid} child {cid} not in nodes"
                    )
                if self.nodes[cid].parent_id != nid:
                    raise ValueError(
                        f"Node {cid} parent_id mismatch: expected {nid}, "
                        f"got {self.nodes[cid].parent_id}"
                    )
        # PR-ROUND-2/C-01: 邻接表一致性校验 — 防止绕过 add_edge 直接 append edges 导致索引不一致
        expected_adj_out: dict[str, list[str]] = {}
        expected_adj_in: dict[str, list[str]] = {}
        for e in self.edges:
            expected_adj_out.setdefault(e.source, []).append(e.target)
            expected_adj_in.setdefault(e.target, []).append(e.source)
        if expected_adj_out != self._adj_out or expected_adj_in != self._adj_in:
            # 自动重建(而不是抛异常,因为 from_dict 已调用 _build_adjacency,此处仅兜底)
            self._build_adjacency()

    def get_layer(self, layer: NodeLayer) -> list[Node]:
        """获取指定层的所有节点"""
        return [n for n in self.nodes.values() if n.layer == layer]

    def get_descendants(self, node_id: str) -> list[str]:
        """获取节点的所有后代(级联删除范围)

        边界场景: 删除级联 — 明确级联范围供前端确认"A及3个下游都会被删"。
        包含 children_ids 递归 + edges 下游。
        """
        if node_id not in self.nodes:
            return []
        result: list[str] = []
        seen: set[str] = set()
        # children_ids 递归(父子层级关系)
        stack = list(self.nodes[node_id].children_ids)
        while stack:
            cid = stack.pop()
            if cid in seen or cid not in self.nodes:
                continue
            seen.add(cid)
            result.append(cid)
            stack.extend(self.nodes[cid].children_ids)
        # edges 下游(同层执行顺序) — PR-Q: 使用邻接表索引替代全量扫描
        stack = [t for t in self._adj_out.get(node_id, []) if t not in seen]
        while stack:
            tid = stack.pop()
            if tid in seen or tid not in self.nodes:
                continue
            seen.add(tid)
            result.append(tid)
            stack.extend(
                t for t in self._adj_out.get(tid, []) if t not in seen
            )
        return result

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
        for _nid, nd in data.get("nodes", {}).items():
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
        # P-LOW-2-2 + R2-06: 加载时去重 — 防止持久化文件已含脏数据
        # R2-06: 直接 append edges,最后一次性 _build_adjacency,避免 add_edge 逐条维护索引
        seen_edge_ids: set[str] = set()
        seen_endpoints: set[tuple[str, str, str]] = set()
        for ed in data.get("edges", []):
            edge = Edge(
                id=ed["id"],
                source=ed["source"],
                target=ed["target"],
                label=ed.get("label", ""),
                edge_type=ed.get("edge_type", "data"),
                metadata=ed.get("metadata", {}),
            )
            # 跳过重复 id 或重复 (source, target, edge_type)
            key = (edge.source, edge.target, edge.edge_type)
            if edge.id in seen_edge_ids or key in seen_endpoints:
                warnings.warn(
                    f"加载边 {edge.id} ({edge.source}->{edge.target}) 重复,已跳过",
                    stacklevel=2,
                )
                continue
            # 端点必须存在(add_edge 已有校验,from_dict 同步保留)
            if edge.source not in g.nodes or edge.target not in g.nodes:
                warnings.warn(
                    f"加载边 {edge.id} 端点不存在,已跳过",
                    stacklevel=2,
                )
                continue
            # R2-06: 直接 append + 索引,跳过 add_edge(避免逐条环检测开销)
            g.edges.append(edge)
            seen_edge_ids.add(edge.id)
            seen_endpoints.add(key)
        # PR-Q + R2-06: 一次性重建邻接表 + _edge_endpoints 索引
        g._build_adjacency()
        # 专家 3 建议: 加载后立即校验图完整性(validate 内部会做环检测)
        g.validate()
        return g
