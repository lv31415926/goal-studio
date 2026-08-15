"""G2: LLMJudgeEvaluator + RatchetGate.judge_reports 双评审判定"""
from __future__ import annotations

from typing import Any

import pytest

from goal_studio.core.contract import Report
from goal_studio.core.llm_judge import LLMJudgeEvaluator, graph_summary
from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeLayer
from goal_studio.core.ratchet_gate import RatchetDecision, RatchetGate
from goal_studio.llm.base import BaseLLMProvider, LLMMessage


def _graph() -> NodeGraph:
    g = NodeGraph(root_id="a")
    for nid in ("a", "b"):
        g.add_node(Node(id=nid, layer=NodeLayer.L1_PLANNING, title=nid, description=f"节点{nid}"))
    g.add_edge(Edge(id="e1", source="a", target="b"))
    return g


class _FakeLLM(BaseLLMProvider):
    def __init__(self, payload: Any = None, exc: Exception | None = None) -> None:
        self.payload = payload
        self.exc = exc
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake"

    def complete_json(self, messages: list[LLMMessage], **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.payload  # type: ignore[no-any-return]


# ==== LLMJudgeEvaluator ====

def test_judge_valid_response() -> None:
    llm = _FakeLLM(payload={
        "score": 87.5,
        "dimensions": {"completeness": 90, "structure": 85},
        "issues": ["缺异常处理"],
        "suggestions": ["补 L3 参数"],
    })
    report = LLMJudgeEvaluator(llm).evaluate(_graph(), goal_text="创建技能")
    assert report is not None
    assert report.score == 87.5
    assert report.evaluator == "llm-judge:reviewer"
    assert report.dimensions["structure"] == 85
    assert report.issues == ["缺异常处理"]
    assert llm.calls == 1


def test_judge_score_clamped() -> None:
    report = LLMJudgeEvaluator(_FakeLLM(payload={"score": 150})).evaluate(_graph())
    assert report is not None and report.score == 100.0
    report = LLMJudgeEvaluator(_FakeLLM(payload={"score": -5})).evaluate(_graph())
    assert report is not None and report.score == 0.0


def test_judge_tiebreaker_persona() -> None:
    judge = LLMJudgeEvaluator(_FakeLLM(payload={"score": 80}), persona="tiebreaker")
    report = judge.evaluate(_graph())
    assert report is not None
    assert report.evaluator == "llm-judge:tiebreaker"


@pytest.mark.parametrize("payload", [
    None,
    {"no_score": 1},
    {"score": "high"},
    {"score": True},
    "not-a-dict",
])
def test_judge_invalid_payload_returns_none(payload: Any) -> None:
    assert LLMJudgeEvaluator(_FakeLLM(payload=payload)).evaluate(_graph()) is None


def test_judge_llm_exception_returns_none() -> None:
    llm = _FakeLLM(exc=RuntimeError("boom"))
    assert LLMJudgeEvaluator(llm).evaluate(_graph()) is None


def test_graph_summary_truncates() -> None:
    g = NodeGraph()
    for i in range(50):
        g.add_node(Node(id=f"n{i}", layer=NodeLayer.L1_PLANNING, title=f"t{i}"))
    s = graph_summary(g, max_nodes=10)
    assert "其余 40 个节点省略" in s
    assert "50 节点" in s


# ==== RatchetGate.judge_reports ====

def _report(score: float, issues: list[str] | None = None) -> Report:
    return Report(passed=score >= 60, score=score,
                  dimensions={"q": score}, issues=issues or [], suggestions=[],
                  evaluator=f"ev-{score}")


def test_judge_reports_single_degrades_to_judge() -> None:
    gate = RatchetGate()
    decision, merged = gate.judge_reports([_report(90.0)], 80.0, _graph())
    assert decision is RatchetDecision.KEEP
    assert merged.score == 90.0


def test_judge_reports_agreement_averages() -> None:
    gate = RatchetGate()
    decision, merged = gate.judge_reports([_report(90.0), _report(84.0)], 80.0, _graph())
    assert decision is RatchetDecision.KEEP
    assert merged.score == pytest.approx(87.0)
    assert merged.evaluator == "peer-merge"
    assert len(gate.history) == 1  # 正常路径记录一轮


def test_judge_reports_conflict_returns_arbitrate_without_record() -> None:
    gate = RatchetGate()
    decision, merged = gate.judge_reports([_report(95.0), _report(70.0)], 80.0, _graph())
    assert decision is RatchetDecision.ARBITRATE
    assert merged.score == pytest.approx(82.5)
    assert gate.history == []  # ARBITRATE 不记录,等 arbitrate 统一记录
    assert gate.current_score == pytest.approx(82.5)


def test_arbitrate_median_then_records() -> None:
    gate = RatchetGate()
    decision, merged = gate.arbitrate(
        _graph(), lambda _g: _report(92.0), [_report(95.0), _report(70.0)], 80.0,
    )
    # median(95, 70, 92) = 92 ≥ 80 → KEEP
    assert decision is RatchetDecision.KEEP
    assert merged.score == 92.0
    assert len(gate.history) == 1  # arbitrate → judge 统一记录


def test_judge_with_peers_backward_compat() -> None:
    """既有 API 保持可用(委托 judge_reports)"""
    gate = RatchetGate()
    decision, merged = gate.judge_with_peers(
        _graph(), [lambda g: _report(90.0), lambda g: _report(88.0)], 80.0,
    )
    assert decision is RatchetDecision.KEEP
    assert merged.score == pytest.approx(89.0)
