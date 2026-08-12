"""RatchetGate: 棘轮门控 + 独立评审调度 + git 回滚

核心原则: 分数只能升,不能降。
  new_score > old_score → KEEP
  new_score <= old_score → REVERT

支持:
  - 独立评审 (评技能的AI必须与改技能的AI不同)
  - 双评审冲突仲裁 (分差 > 15 触发第三评审)
  - 早停 (连续2轮 Δ < 2 终止)
  - git revert 回滚(保留历史)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from goal_studio.core.contract import Report
from goal_studio.core.node_graph import NodeGraph


class RatchetDecision(str, Enum):
    """棘轮判定结果"""

    KEEP = "keep"
    """new > old,保留修改"""

    REVERT = "revert"
    """new <= old,回滚"""

    EARLY_STOP = "early_stop"
    """连续2轮 Δ < 2,提前终止"""

    ARBITRATE = "arbitrate"
    """双评审分差过大,需第三评审仲裁"""


class Evaluator(Protocol):
    """评估器协议: 接受 NodeGraph 返回 Report"""

    def __call__(self, graph: NodeGraph) -> Report: ...


@dataclass
class RoundRecord:
    """每轮记录"""

    round: int
    old_score: float
    new_score: float
    delta: float
    decision: RatchetDecision
    issues: list[str] = field(default_factory=list)


@dataclass
class RatchetGate:
    """棘轮门控

    Args:
        threshold: KEEP 阈值,默认 new > old 即 KEEP
        early_stop_delta: 连续2轮 Δ < 此值则早停,默认 2.0
        conflict_threshold: 双评审分差超过此值触发仲裁,默认 15.0
    """

    threshold: float = 0.0
    early_stop_delta: float = 2.0
    conflict_threshold: float = 15.0

    history: list[RoundRecord] = field(default_factory=list)
    best_score: float = 0.0
    best_graph: NodeGraph | None = None

    def judge(
        self,
        old_score: float,
        new_score: float,
        current_graph: NodeGraph,
        previous_graph: NodeGraph | None = None,
    ) -> RatchetDecision:
        """棘轮判定

        Args:
            old_score: 修改前分数
            new_score: 修改后分数
            current_graph: 修改后图(若 KEEP 则成为 best_graph)
            previous_graph: 修改前图(若 REVERT 则恢复)

        Returns:
            RatchetDecision
        """
        delta = new_score - old_score

        # 早停检测
        if len(self.history) >= 2:
            last_two = self.history[-2:]
            if (
                abs(last_two[-1].delta) < self.early_stop_delta
                and abs(last_two[-2].delta) < self.early_stop_delta
            ):
                decision = RatchetDecision.EARLY_STOP
                self._record(old_score, new_score, delta, decision)
                return decision

        # 棘轮判定
        if new_score > old_score + self.threshold:
            decision = RatchetDecision.KEEP
            if new_score > self.best_score:
                self.best_score = new_score
                self.best_graph = current_graph
        else:
            decision = RatchetDecision.REVERT

        self._record(old_score, new_score, delta, decision)
        return decision

    def judge_with_peers(
        self,
        graph: NodeGraph,
        evaluators: list[Evaluator],
        old_score: float,
    ) -> tuple[RatchetDecision, Report]:
        """双独立评审判定

        两个评估器分别评分:
          - 分差 <= conflict_threshold → 取平均
          - 分差 > conflict_threshold → 返回 ARBITRATE,需调用 arbitrate()

        Args:
            graph: 待评估图
            evaluators: 2 个独立评估器
            old_score: 修改前分数

        Returns:
            (decision, report)
        """
        if len(evaluators) != 2:
            raise ValueError("Need exactly 2 peer evaluators")

        reports = [ev(graph) for ev in evaluators]
        scores = [r.score for r in reports]

        if abs(scores[0] - scores[1]) > self.conflict_threshold:
            # 合并报告用于仲裁展示
            merged = self._merge_reports(reports)
            return RatchetDecision.ARBITRATE, merged

        avg_score = sum(scores) / len(scores)
        merged = self._merge_reports(reports)
        merged.score = avg_score

        decision = self.judge(old_score, avg_score, graph)
        return decision, merged

    def arbitrate(
        self,
        graph: NodeGraph,
        third_evaluator: Evaluator,
        previous_reports: list[Report],
        old_score: float,
    ) -> tuple[RatchetDecision, Report]:
        """第三评审仲裁(分差过大时调用)"""
        third_report = third_evaluator(graph)
        all_reports = previous_reports + [third_report]
        # 取中位数
        sorted_scores = sorted(r.score for r in all_reports)
        median_score = sorted_scores[len(sorted_scores) // 2]
        merged = self._merge_reports(all_reports)
        merged.score = median_score
        decision = self.judge(old_score, median_score, graph)
        return decision, merged

    def _record(
        self,
        old_score: float,
        new_score: float,
        delta: float,
        decision: RatchetDecision,
    ) -> None:
        self.history.append(
            RoundRecord(
                round=len(self.history) + 1,
                old_score=old_score,
                new_score=new_score,
                delta=delta,
                decision=decision,
            )
        )

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


# git 回滚钩子(可选,由 SessionManager 或 CLI 注入)
class GitRollbackHook(Protocol):
    def revert_head(self, message: str) -> None: ...


_ = Any  # keep import
_ = Callable
