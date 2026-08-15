"""RatchetGate: 棘轮门控 + 版本快照 + 独立评审 + 回滚保护

核心原则: 分数只能升,不能降。
  new_score >= old_score → KEEP (保留 + 更新 best, 不降即保留)
  new_score < old_score → REVERT (回滚到快照)
  连续2轮 Δ < 2 → EARLY_STOP (终止 Loop)
  双评审分差 > 15 → ARBITRATE (第三评审中位数)

支持:
  - 版本快照栈 (LIFO): refine 前保存,REVERT 时恢复
  - 独立评审 (评技能的AI必须与改技能的AI不同)
  - 双评审冲突仲裁 (分差 > 15 触发第三评审)
  - 早停 (连续2轮 Δ < 2 终止)
  - undo (撤销最近一次 refine)
  - restore_best (跳转到历史最佳版本)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from goal_studio.core.contract import Report
from goal_studio.core.node_graph import NodeGraph


class RatchetDecision(str, Enum):
    """棘轮判定结果"""

    KEEP = "keep"
    """new > old,保留修改"""

    REVERT = "revert"
    """new <= old,回滚到快照"""

    EARLY_STOP = "early_stop"
    """连续2轮 Δ < 2,提前终止"""

    ARBITRATE = "arbitrate"
    """双评审分差过大,需第三评审仲裁"""


class Evaluator(Protocol):
    """评估器协议: 接受 NodeGraph 返回 Report"""

    def __call__(self, graph: NodeGraph) -> Report: ...


@dataclass
class GraphSnapshot:
    """版本快照 — refine 前的图状态"""

    version: int
    """快照版本号"""

    graph: NodeGraph
    """快照时的 NodeGraph"""

    score: float
    """快照时的分数"""

    patch_summary: str
    """触发此次 refine 的 patch 摘要"""

    timestamp: str = ""
    """快照时间 ISO 格式"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "graph": self.graph.to_dict(),
            "score": self.score,
            "patch_summary": self.patch_summary,
            "timestamp": self.timestamp,
        }


@dataclass
class RoundRecord:
    """每轮棘轮记录"""

    round: int
    old_score: float
    new_score: float
    delta: float
    decision: RatchetDecision
    patch_summary: str = ""
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "old_score": self.old_score,
            "new_score": self.new_score,
            "delta": self.delta,
            "decision": self.decision.value,
            "patch_summary": self.patch_summary,
            "issues": self.issues,
        }


@dataclass
class RatchetGate:
    """棘轮门控

    Args:
        threshold: KEEP 阈值,默认 new > old 即 KEEP
        early_stop_delta: 连续2轮 Δ < 此值则早停,默认 2.0
        conflict_threshold: 双评审分差超过此值触发仲裁,默认 15.0
        max_snapshots: 快照栈最大容量,默认 20
    """

    # R2-08: history 最大长度(防止长期会话无限增长)
    MAX_HISTORY: int = 100

    threshold: float = 0.0
    early_stop_delta: float = 2.0
    conflict_threshold: float = 15.0
    max_snapshots: int = 20

    history: list[RoundRecord] = field(default_factory=list)
    """棘轮判定历史"""

    snapshots: list[GraphSnapshot] = field(default_factory=list)
    """版本快照栈 (LIFO)"""

    # R2-09: 单调递增快照版本计数器(避免 len(snapshots)+1 在修剪后回退)
    _snapshot_counter: int = field(default=0, repr=False, compare=False)

    best_score: float = float("-inf")
    """历史最高分(R2-07: 初始 -inf,首次任意分数 > -inf 即更新 best_graph)"""

    best_graph: NodeGraph | None = None
    """历史最佳图"""

    current_score: float = 0.0
    """当前图分数"""

    def save_snapshot(
        self, graph: NodeGraph, score: float, patch_summary: str = ""
    ) -> GraphSnapshot:
        """refine 前保存快照

        Args:
            graph: 当前图(refine 前的)
            score: 当前分数
            patch_summary: 即将应用的 patch 摘要

        Returns:
            创建的 GraphSnapshot
        """
        # R2-09: 用单调计数器生成 version,避免修剪快照后 len(snapshots)+1 回退
        version = self._snapshot_counter + 1
        self._snapshot_counter += 1
        snapshot = GraphSnapshot(
            version=version,
            graph=NodeGraph.from_dict(graph.to_dict()),
            score=score,
            patch_summary=patch_summary,
            timestamp=datetime.now(UTC).isoformat(),
        )
        self.snapshots.append(snapshot)
        # 修剪快照栈
        if len(self.snapshots) > self.max_snapshots:
            self.snapshots = self.snapshots[-self.max_snapshots:]
        return snapshot

    def judge(
        self,
        old_score: float,
        new_score: float,
        current_graph: NodeGraph,
        patch_summary: str = "",
    ) -> RatchetDecision:
        """棘轮判定

        Args:
            old_score: 修改前分数
            new_score: 修改后分数
            current_graph: 修改后图(若 KEEP 则更新 best)
            patch_summary: patch 摘要(记录用)

        Returns:
            RatchetDecision
        """
        delta = new_score - old_score
        self.current_score = new_score

        # 棘轮判定: new >= old + threshold → KEEP (不降即保留,避免 Δ=0 时误回滚用户修改)
        if new_score >= old_score + self.threshold:
            decision = RatchetDecision.KEEP
            # 更新历史最佳
            if new_score > self.best_score:
                self.best_score = new_score
                self.best_graph = NodeGraph.from_dict(current_graph.to_dict())
        else:
            decision = RatchetDecision.REVERT
            # REVERT 时恢复 current_score 为 old_score
            self.current_score = old_score

        self._record(old_score, new_score, delta, decision, patch_summary)

        # 早停检测: 连续2轮(含当前轮) delta 为正且 < early_stop_delta (改进停滞)
        # 在 _record 之后检查,使当前轮纳入判定;不含 abs 避免 REVERT(负delta)被误判为停滞
        if len(self.history) >= 2:
            last_delta = self.history[-1].delta
            prev_delta = self.history[-2].delta
            if (0 <= last_delta < self.early_stop_delta
                    and 0 <= prev_delta < self.early_stop_delta):
                decision = RatchetDecision.EARLY_STOP
                # PR-ROUND-2/C-07: 同步更新 history 最后一条记录的 decision,
                # 保持 history 与返回值一致(否则 UI 展示 KEEP 但实际 EARLY_STOP)
                self.history[-1].decision = RatchetDecision.EARLY_STOP

        return decision

    def undo(self) -> GraphSnapshot | None:
        """撤销最近一次 refine — 弹出栈顶快照

        Returns:
            弹出的快照(用于恢复 graph),无快照时返回 None
        """
        if not self.snapshots:
            return None
        return self.snapshots.pop()

    def restore_best(self) -> NodeGraph | None:
        """恢复到历史最佳版本

        Returns:
            best_graph 的深拷贝,无最佳时返回 None。
            PR-M: 返回深拷贝避免调用方修改污染 best_graph 快照。
        """
        if self.best_graph:
            self.current_score = self.best_score
            # 返回深拷贝,避免污染 best_graph
            return NodeGraph.from_dict(self.best_graph.to_dict())
        return None

    def get_last_snapshot(self) -> GraphSnapshot | None:
        """获取栈顶快照(不弹出)"""
        return self.snapshots[-1] if self.snapshots else None

    def judge_reports(
        self,
        reports: list[Report],
        old_score: float,
        current_graph: NodeGraph,
        patch_summary: str = "",
    ) -> tuple[RatchetDecision, Report]:
        """对已完成的评审报告做双评审棘轮判定(不重复调用评估器)

        G2 双评估器接入点: 调用方(LLM 评审昂贵,不重复执行)先产出报告,
        再统一走本方法。语义与 judge_with_peers 一致:
          - 分差 <= conflict_threshold → 取平均进棘轮
          - 分差 > conflict_threshold → 返回 ARBITRATE(不 _record),
            由调用方补第三评审后调 arbitrate()

        单报告时等价于普通 judge(降级路径)。
        """
        if not reports:
            raise ValueError("Need at least 1 report")
        scores = [r.score for r in reports]

        if len(reports) >= 2 and (max(scores) - min(scores)) > self.conflict_threshold:
            merged = self._merge_reports(reports)
            # R2-10: ARBITRATE 不调用 _record,仅设置 current_score
            # 让后续 arbitrate → judge 统一记录,避免重复 _record
            self.current_score = sum(scores) / len(scores)
            return RatchetDecision.ARBITRATE, merged

        avg_score = sum(scores) / len(scores)
        merged = self._merge_reports(reports)
        merged.score = avg_score

        decision = self.judge(old_score, avg_score, current_graph, patch_summary)
        return decision, merged

    def judge_with_peers(
        self,
        graph: NodeGraph,
        evaluators: list[Evaluator],
        old_score: float,
        patch_summary: str = "",
    ) -> tuple[RatchetDecision, Report]:
        """双独立评审判定

        两个评估器分别评分:
          - 分差 <= conflict_threshold → 取平均
          - 分差 > conflict_threshold → 返回 ARBITRATE,需调用 arbitrate()

        Args:
            graph: 待评估图
            evaluators: 2 个独立评估器
            old_score: 修改前分数
            patch_summary: patch 摘要

        Returns:
            (decision, report)
        """
        if len(evaluators) != 2:
            raise ValueError("Need exactly 2 peer evaluators")
        reports = [ev(graph) for ev in evaluators]
        return self.judge_reports(reports, old_score, graph, patch_summary)

    def arbitrate(
        self,
        graph: NodeGraph,
        third_evaluator: Evaluator,
        previous_reports: list[Report],
        old_score: float,
        patch_summary: str = "",
    ) -> tuple[RatchetDecision, Report]:
        """第三评审仲裁(分差过大时调用)"""
        third_report = third_evaluator(graph)
        all_reports = previous_reports + [third_report]
        # 取中位数
        sorted_scores = sorted(r.score for r in all_reports)
        median_score = sorted_scores[len(sorted_scores) // 2]
        merged = self._merge_reports(all_reports)
        merged.score = median_score
        decision = self.judge(old_score, median_score, graph, patch_summary)
        return decision, merged

    def _record(
        self,
        old_score: float,
        new_score: float,
        delta: float,
        decision: RatchetDecision,
        patch_summary: str = "",
    ) -> None:
        self.history.append(
            RoundRecord(
                round=len(self.history) + 1,
                old_score=old_score,
                new_score=new_score,
                delta=delta,
                decision=decision,
                patch_summary=patch_summary,
            )
        )
        # R2-08: 修剪 history(保留最近 MAX_HISTORY 条)
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[-self.MAX_HISTORY:]

    @staticmethod
    def _merge_reports(reports: list[Report]) -> Report:
        """合并多个评估报告"""
        all_issues: list[str] = []
        all_suggestions: list[str] = []
        all_dims: dict[str, list[float]] = {}
        for r in reports:
            all_issues.extend(r.issues)
            all_suggestions.extend(r.suggestions)
            for k, v in r.dimensions.items():
                all_dims.setdefault(k, []).append(v)
        merged_dims = {k: sum(v) / len(v) for k, v in all_dims.items()}
        avg_passed = sum(1 for r in reports if r.passed) > len(reports) / 2
        return Report(
            passed=avg_passed,
            score=sum(r.score for r in reports) / len(reports),
            dimensions=merged_dims,
            issues=all_issues,
            suggestions=all_suggestions,
            evaluator="peer-merge",
        )

    @property
    def can_undo(self) -> bool:
        """是否可撤销(P-LOW-2-3)

        reload 后 snapshots 为空(完整 graph 不持久化),can_undo 返回 False。
        UI 据此禁用撤销按钮,避免误导用户。
        """
        return len(self.snapshots) > 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RatchetGate:
        """从 dict 重建 RatchetGate(P-LOW-2-3)

        重建 threshold/early_stop_delta/conflict_threshold/best_score/current_score/history。
        注意: snapshots 不重建(完整 graph 持久化成本高),can_undo 将返回 False。
        best_graph 由 SessionManager.from_dict 从 best_graph 字段单独恢复。
        """
        g = cls(
            threshold=data.get("threshold", 0.0),
            early_stop_delta=data.get("early_stop_delta", 2.0),
            conflict_threshold=data.get("conflict_threshold", 15.0),
        )
        g.best_score = data.get("best_score", float("-inf"))
        g.current_score = data.get("current_score", 0.0)
        for r in data.get("history", []):
            g.history.append(
                RoundRecord(
                    round=r.get("round", 0),
                    old_score=r.get("old_score", 0.0),
                    new_score=r.get("new_score", 0.0),
                    delta=r.get("delta", 0.0),
                    decision=RatchetDecision(r.get("decision", "keep")),
                    patch_summary=r.get("patch_summary", ""),
                    issues=r.get("issues", []),
                )
            )
        # R2-09: 从 snapshots 数量恢复单调计数器
        snapshots_data = data.get("snapshots", [])
        g._snapshot_counter = len(snapshots_data)
        return g

    def to_dict(self) -> dict[str, Any]:
        """序列化(不含 graph 对象,只含摘要)"""
        return {
            "threshold": self.threshold,
            "early_stop_delta": self.early_stop_delta,
            "conflict_threshold": self.conflict_threshold,
            "best_score": self.best_score,
            "current_score": self.current_score,
            "history": [r.to_dict() for r in self.history],
            "snapshots": [
                {
                    "version": s.version,
                    "score": s.score,
                    "patch_summary": s.patch_summary,
                    "timestamp": s.timestamp,
                }
                for s in self.snapshots
            ],
        }


# git 回滚钩子(可选,由 SessionManager 或 CLI 注入)
class GitRollbackHook(Protocol):
    def revert_head(self, message: str) -> None: ...
