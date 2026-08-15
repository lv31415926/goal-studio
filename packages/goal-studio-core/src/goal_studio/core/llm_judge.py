"""LLMJudgeEvaluator: LLM-as-a-judge 独立评审 (G2 双评估器)

设计依据 — 验证器不对称 (DeepVerifier / CoRefine 线):
检验比生成便宜,独立轻量验证器的单位收益高于升级生成器。
规则评估器(adapter.validate) + LLM 评审形成双评审:
  - 分差 <= conflict_threshold(默认 15) → 取平均进棘轮
  - 分差 > 15 → 第三评审(tiebreaker persona)仲裁取中位数

降级契约: evaluate() 在 LLM 不可用/返回不可解析时返回 None,
调用方回退单评估器路径 — 评审失败不阻断精化主流程。
"""
from __future__ import annotations

import logging
from typing import Any

from goal_studio.core.contract import Report
from goal_studio.core.node_graph import NodeGraph
from goal_studio.llm.base import BaseLLMProvider, LLMMessage

_logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是 goal-studio 的独立评审官,与规则评估器互相独立。
对给定的三层创作 DAG 按以下 rubric 打分(0-100):

1. completeness 完整性: L1 任务覆盖目标全流程,无明显缺环
2. structure 结构性: 三层职责清晰(L1 规划/L2 分配/L3 精化),父子关系合理
3. executability 可执行性: 节点描述具体,关键节点 slots 有可操作参数
4. alignment 目标对齐: 节点内容与用户目标/约束一致,无跑偏

输出严格的 JSON(不要其他文字):
{"score": <0-100 总分>,
 "dimensions": {"completeness": <0-100>, "structure": <0-100>,
                "executability": <0-100>, "alignment": <0-100>},
 "issues": ["具体问题,最多5条"],
 "suggestions": ["改进建议,最多3条"]}"""

TIEBREAKER_SYSTEM_PROMPT = """你是 goal-studio 的仲裁评审官。
两位独立评审对同一 DAG 的打分分差过大,你需要独立给出仲裁分(0-100),
不受已有分数影响,只依据 DAG 本身质量与目标对齐度。

输出严格的 JSON(不要其他文字):
{"score": <0-100>, "dimensions": {...}, "issues": [...], "suggestions": [...]}"""


def graph_summary(graph: NodeGraph, max_nodes: int = 40) -> str:
    """紧凑图摘要(供评审 prompt),超大图截断保护 token"""
    lines = [
        f"图规模: {len(graph.nodes)} 节点, {len(graph.edges)} 边, 版本 v{graph.version}",
        "节点列表 (id | 层 | 类型 | 标题 | 描述):",
    ]
    for i, n in enumerate(graph.nodes.values()):
        if i >= max_nodes:
            lines.append(f"  ... 其余 {len(graph.nodes) - max_nodes} 个节点省略")
            break
        desc = (n.description or "")[:60]
        lines.append(f"  {n.id} | L{n.layer.value} | {n.node_type} | {n.title} | {desc}")
    return "\n".join(lines)


class LLMJudgeEvaluator:
    """LLM 独立评审器(persona=reviewer 常规评审 / tiebreaker 仲裁)"""

    def __init__(self, llm: BaseLLMProvider, persona: str = "reviewer") -> None:
        self._llm = llm
        self._persona = persona

    def evaluate(self, graph: NodeGraph, goal_text: str = "") -> Report | None:
        """评审 DAG,失败返回 None(调用方降级)"""
        system = (
            TIEBREAKER_SYSTEM_PROMPT
            if self._persona == "tiebreaker"
            else JUDGE_SYSTEM_PROMPT
        )
        user_parts = []
        if goal_text:
            user_parts.append(f"用户目标: {goal_text[:300]}")
        user_parts.append("待评审 DAG:")
        user_parts.append(graph_summary(graph))
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content="\n".join(user_parts)),
        ]
        try:
            data = self._llm.complete_json(messages, temperature=0.1, max_tokens=2048)
        except Exception as e:  # noqa: BLE001 — 评审失败降级,不阻断精化
            _logger.warning(
                "llm_judge_failed",
                extra={"persona": self._persona, "err_type": type(e).__name__},
            )
            return None
        return self._parse_report(data)

    def _parse_report(self, data: Any) -> Report | None:
        """防御性解析 LLM 评审 JSON,字段缺失/类型错乱返回 None"""
        if not isinstance(data, dict):
            return None
        raw_score = data.get("score")
        if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
            return None
        score = max(0.0, min(100.0, float(raw_score)))

        dimensions: dict[str, float] = {}
        raw_dims = data.get("dimensions")
        if isinstance(raw_dims, dict):
            for k, v in raw_dims.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    dimensions[str(k)] = max(0.0, min(100.0, float(v)))

        def _str_list(key: str, limit: int) -> list[str]:
            raw = data.get(key)
            if not isinstance(raw, list):
                return []
            return [str(x)[:200] for x in raw if x is not None][:limit]

        return Report(
            passed=score >= 60,
            score=score,
            dimensions=dimensions,
            issues=_str_list("issues", 5),
            suggestions=_str_list("suggestions", 3),
            evaluator=f"llm-judge:{self._persona}",
        )
