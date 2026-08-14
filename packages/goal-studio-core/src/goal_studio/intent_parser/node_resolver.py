"""Layer 4: 节点引用消解 (三信号融合算法)

基于 POLAR + 共指消解 + 视觉接地三路径迁移。
score = w1·recency + w2·hover + w3·selection + w4·semantic + w5·spatial + w6·property
置信度差 < 0.2 → 触发澄清请求
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from goal_studio.core.node_graph import Node, NodeGraph


@dataclass
class ResolveResult:
    """节点引用解析结果"""
    ok: bool
    node_id: str | None = None
    reason: str = ""
    clarification_options: list[str] | None = None
    confidence: float = 0.0


class NodeReferenceResolver:
    """Layer 4: 节点引用解析器

    三信号融合:
      - 信号 A: 显式 ID/name 匹配 (w=1.0)
      - 信号 B: 当前选中 (w=0.4)
      - 信号 C: 鼠标悬停 (w=0.35)
      - 信号 D: 最近编辑栈 (w=0.15, 越近越高)
      - 信号 E: 语义相似度 (w=0.25)
      - 信号 F: 空间语言 (w=0.3)
      - 信号 G: 属性描述 (w=0.3)
    """

    # 权重配置
    W_EXPLICIT = 1.0
    W_SELECTION = 0.4
    W_HOVER = 0.35
    W_RECENCY = 0.15
    W_SEMANTIC = 0.25
    W_SPATIAL = 0.3
    W_PROPERTY = 0.3

    AMBIGUITY_THRESHOLD = 0.2

    def __init__(
        self,
        graph: NodeGraph,
        recent_stack: list[str] | None = None,
        current_selection: str | None = None,
        current_hover: str | None = None,
        mouse_position: tuple[float, float] | None = None,
        viewport: tuple[float, float, float] | None = None,  # (x, y, zoom)
    ) -> None:
        self.graph = graph
        self.recent_stack = recent_stack or []
        self.selection = current_selection
        self.hover = current_hover
        self.mouse_pos = mouse_position
        self.viewport = viewport

    def resolve(
        self,
        ref_type: str,
        ref_value: str,
        asr_confidence: float = 0.9,
    ) -> ResolveResult:
        """解析节点引用

        Args:
            ref_type: id/name/spatial/property/pronoun
            ref_value: 引用值
            asr_confidence: ASR 置信度(折扣分数)

        Returns:
            ResolveResult
        """
        candidates = self._generate_candidates(ref_type, ref_value)
        if not candidates:
            return ResolveResult(
                ok=False,
                reason=f"找不到匹配节点: {ref_value}",
            )

        scored = [
            (node, self._score_node(node, ref_type, ref_value) * asr_confidence)
            for node in candidates
        ]
        scored.sort(key=lambda x: -x[1])

        if len(scored) == 1:
            return ResolveResult(
                ok=True,
                node_id=scored[0][0].id,
                confidence=scored[0][1],
            )

        top1, top2 = scored[0], scored[1]
        if top1[1] - top2[1] < self.AMBIGUITY_THRESHOLD:
            options = [n.id for n, _ in scored[:5]]
            return ResolveResult(
                ok=False,
                reason="你指的是哪个节点?",
                clarification_options=options,
                confidence=top1[1],
            )

        return ResolveResult(
            ok=True,
            node_id=top1[0].id,
            confidence=top1[1],
        )

    # 边界场景: 大规模DAG 过滤阈值
    LARGE_DAG_THRESHOLD = 100
    LARGE_DAG_CANDIDATE_CAP = 20

    def _generate_candidates(self, ref_type: str, ref_value: str) -> list[Node]:
        """生成候选节点

        边界场景: 大规模DAG(>100) — pronoun 模式不再返回全节点,
        而是先按 selection/hover/recency 过滤到 cap 个再精排,避免 O(n²) 打分。
        """
        all_nodes = list(self.graph.nodes.values())
        value = ref_value.strip().lower()

        if ref_type == "id":
            return [n for n in all_nodes if n.id.lower() == value]

        if ref_type == "name":
            return [
                n for n in all_nodes
                if value in n.title.lower() or value in n.description.lower()
            ]

        if ref_type == "spatial":
            return self._spatial_filter(ref_value)

        if ref_type == "property":
            return self._property_filter(ref_value)

        # pronoun → 大规模DAG 先过滤再精排
        if len(all_nodes) > self.LARGE_DAG_THRESHOLD:
            return self._pronoun_filter_large(value)

        return all_nodes

    def _pronoun_filter_large(self, value: str) -> list[Node]:
        """大规模DAG pronoun 候选过滤

        优先级: selection > hover > recent_stack > 同层前缀匹配 > 取前 cap 个
        """
        candidates: list[Node] = []
        seen: set[str] = set()

        def _add(node_id: str | None) -> None:
            if node_id and node_id in self.graph.nodes and node_id not in seen:
                seen.add(node_id)
                candidates.append(self.graph.nodes[node_id])

        # 1. 当前选中/悬停
        _add(self.selection)
        _add(self.hover)
        # 2. 最近编辑栈
        for nid in self.recent_stack:
            _add(nid)
            if len(candidates) >= self.LARGE_DAG_CANDIDATE_CAP:
                break
        # 3. 标题/描述前缀匹配(若有 value)
        if value and len(candidates) < self.LARGE_DAG_CANDIDATE_CAP:
            for n in self.graph.nodes.values():
                if n.id in seen:
                    continue
                if value in n.title.lower() or value in n.description.lower():
                    _add(n.id)
                    if len(candidates) >= self.LARGE_DAG_CANDIDATE_CAP:
                        break
        # 4. 仍不足 → 取前 cap 个(按 id 排序保证稳定)
        if len(candidates) < self.LARGE_DAG_CANDIDATE_CAP:
            for n in sorted(self.graph.nodes.values(), key=lambda x: x.id):
                _add(n.id)
                if len(candidates) >= self.LARGE_DAG_CANDIDATE_CAP:
                    break
        return candidates

    def _score_node(self, node: Node, ref_type: str, ref_value: str) -> float:
        """多信号打分"""
        score = 0.0
        value = ref_value.strip().lower()

        # 信号 A: 显式匹配
        if ref_type == "id":
            if node.id.lower() == value:
                return 1.0
        elif ref_type == "name":
            if value in node.title.lower():
                score += self.W_EXPLICIT
            elif value in node.description.lower():
                score += self.W_EXPLICIT * 0.8

        # 信号 B: 当前选中
        if node.id == self.selection:
            score += self.W_SELECTION

        # 信号 C: 鼠标悬停
        if node.id == self.hover:
            score += self.W_HOVER

        # 信号 D: 最近编辑栈(越近权重越高)
        if node.id in self.recent_stack:
            idx = self.recent_stack.index(node.id)
            recency = 1.0 - (idx / max(len(self.recent_stack), 1))
            score += self.W_RECENCY * recency

        # 信号 E: 语义相似度
        if ref_type in ("name", "property") and value:
            title_sim = self._similarity(value, node.title.lower())
            desc_sim = self._similarity(value, node.description.lower())
            score += self.W_SEMANTIC * max(title_sim, desc_sim)

        # 信号 F: 空间语言
        if ref_type == "spatial":
            spatial_score = self._eval_spatial(ref_value, node)
            score += self.W_SPATIAL * spatial_score

        # 信号 G: 属性描述
        if ref_type == "property":
            prop_score = self._eval_property(ref_value, node)
            score += self.W_PROPERTY * prop_score

        # R2-19: 用 sigmoid-like 归一化(1.0 - exp(-score))保留区分度,
        # 替代 min(score, 1.0) 在 score > 1 时全部塌缩到 1.0 的问题
        return 1.0 - math.exp(-score)

    def _similarity(self, a: str, b: str) -> float:
        """相似度计算(PR-ROUND-2/C-09: 字符级 2-gram + 子串匹配)

        原实现按空格分词的 Jaccard 对中文失效(中文无空格,整句作为一个 token)。
        改用字符级 2-gram Jaccard + 子串包含奖励,覆盖中英文混合场景。
        Phase 2 可进一步接 embedding。
        """
        if not a or not b:
            return 0.0
        # 子串包含奖励(如"需求"在"需求分析"中)
        substr_score = 0.0
        if a in b or b in a:
            substr_score = 0.5
        # 字符级 2-gram Jaccard
        def _bigrams(s: str) -> set[str]:
            if len(s) < 2:
                return {s} if s else set()
            return {s[i:i+2] for i in range(len(s) - 1)}
        set_a = _bigrams(a)
        set_b = _bigrams(b)
        if not set_a or not set_b:
            return substr_score
        inter = set_a & set_b
        union = set_a | set_b
        jaccard = len(inter) / len(union) if union else 0.0
        # 取 Jaccard 和子串奖励的较大值
        return max(jaccard, substr_score)

    def _spatial_filter(self, expr: str) -> list[Node]:
        """空间语言过滤"""
        # MVP: 返回所有节点,由 _eval_spatial 打分
        return list(self.graph.nodes.values())

    def _eval_spatial(self, expr: str, node: Node) -> float:
        """评估空间语言匹配度"""
        expr_lower = expr.lower()
        nx, ny = node.position

        # R2-20: 参考点优先用鼠标位置,无鼠标位置时用视口中心
        ref_x, ref_y = 0.0, 0.0
        if self.mouse_pos:
            ref_x, ref_y = self.mouse_pos
        elif self.viewport:
            # viewport = (x, y, zoom),用前两位作为视口中心
            ref_x = self.viewport[0] if self.viewport else 0
            ref_y = self.viewport[1] if self.viewport else 0

        # R2-20: 用画布实际边界(遍历节点求 min/max)替代固定 /1000
        positions = [n.position for n in self.graph.nodes.values()]
        if positions:
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            x_range = max(xs) - min(xs) if max(xs) > min(xs) else 1.0
            y_range = max(ys) - min(ys) if max(ys) > min(ys) else 1.0
        else:
            x_range = y_range = 1000.0

        score = 0.0
        if any(kw in expr_lower for kw in ["左边", "left", "左侧"]):
            if nx < ref_x:
                score = max(score, 1.0 - abs(nx - ref_x) / x_range)
        if any(kw in expr_lower for kw in ["右边", "right", "右侧"]):
            if nx > ref_x:
                score = max(score, 1.0 - abs(nx - ref_x) / x_range)
        if any(kw in expr_lower for kw in ["上面", "上方", "above", "top"]):
            if ny < ref_y:
                score = max(score, 1.0 - abs(ny - ref_y) / y_range)
        if any(kw in expr_lower for kw in ["下面", "下方", "below", "bottom"]):
            if ny > ref_y:
                score = max(score, 1.0 - abs(ny - ref_y) / y_range)
        return score

    def _property_filter(self, expr: str) -> list[Node]:
        """属性描述过滤"""
        # MVP: 返回所有节点
        return list(self.graph.nodes.values())

    def _eval_property(self, expr: str, node: Node) -> float:
        """评估属性描述匹配度"""
        expr_lower = expr.lower()
        score = 0.0

        # 状态属性
        if "高优先级" in expr_lower or "high priority" in expr_lower:
            priority = node.slots.get("priority", "medium")
            if priority == "high":
                score = max(score, 1.0)

        # 颜色属性 (从 metadata)
        if any(c in expr_lower for c in ["红色", "red"]):
            color = node.metadata.get("color", "")
            if color == "red":
                score = max(score, 1.0)

        # 类型属性
        if "任务" in expr_lower and node.node_type == "task":
            score = max(score, 0.8)
        if "agent" in expr_lower and node.node_type == "agent":
            score = max(score, 0.8)

        return score


_ = Any
