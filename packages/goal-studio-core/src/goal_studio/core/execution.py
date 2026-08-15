"""ExecutionEngine: DAG 执行引擎 — grounded validation 落地

把「图看起来对」升级为「图跑得通」: 按拓扑序实际执行 NodeGraph,
条件边真实求值,产出逐节点执行记录。

设计要点:
  - NodeExecutor / EdgeRouter 均为 Protocol,适配器可注入真实执行逻辑
    (默认 DefaultNodeExecutor 模拟执行 + ConditionRouter 条件求值)
  - 分支语义: 同源多条 condition 边按上下文求值,未命中的分支下游标 SKIPPED
  - 失败语义: 节点 FAILED → 下游级联 SKIPPED,总状态 FAILED(不静默吞错)
  - 接地语义: 条件引用的键在上下文中不存在 → ABORTED 并给出诊断
    (这正是 grounded validation 要抓的设计缺陷)
  - 上下文发布规则: 节点输出同时写入 node.id / 归一化标题 / 扁平化 slot 键,
    使「校验通过 == true」这类可读条件可直接对节点 slots 求值
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from goal_studio.core.node_graph import Edge, Node, NodeGraph, NodeStatus


class ExecutionStatus(str, Enum):
    """执行状态(节点级与整图级共用)"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    ABORTED = "aborted"


class ExecutionError(Exception):
    """节点执行失败"""


class ConditionKeyError(Exception):
    """条件引用的上下文键不存在 — 接地校验缺陷"""

    def __init__(self, key: str, expr: str) -> None:
        self.key = key
        self.expr = expr
        super().__init__(
            f"条件「{expr}」引用的键「{key}」未在上下文中定义"
            "(请通过节点 slots 或初始 context 发布该键)"
        )


@dataclass
class ExecutionRecord:
    """单节点执行记录"""

    node_id: str
    title: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    detail: str = ""
    """补充说明(SKIPPED 原因 / 条件求值详情等)"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round(self.duration_ms, 3),
            "output": self.output,
            "error": self.error,
            "detail": self.detail,
        }


@dataclass
class ExecutionSummary:
    """整图执行总结"""

    status: ExecutionStatus = ExecutionStatus.PENDING
    records: list[ExecutionRecord] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    stats: dict[str, int] = field(default_factory=dict)
    """{total, executed, skipped, failed}"""

    @property
    def passed(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "passed": self.passed,
            "error": self.error,
            "stats": self.stats,
            "records": [r.to_dict() for r in self.records],
        }


class NodeExecutor(Protocol):
    """节点执行器协议: 执行单节点,返回发布到上下文的输出"""

    def execute(self, node: Node, context: dict[str, Any]) -> dict[str, Any]: ...


class EdgeRouter(Protocol):
    """边路由协议: 判断边是否激活(条件求值)"""

    def route(self, edge: Edge, context: dict[str, Any]) -> bool: ...


# === 条件求值(安全,无 eval) ===

_OP_PATTERN = re.compile(
    r"^\s*(?P<key>.+?)\s*(?P<op>==|!=|>=|<=|>|<)\s*(?P<value>.+?)\s*$"
)
_BARE_PATTERN = re.compile(r"^\s*(?P<key>[^\s]+)\s*$")

_BOOL_TRUE = {"true", "yes", "是", "通过", "成功"}
_BOOL_FALSE = {"false", "no", "否", "失败"}


def _norm_key(s: str) -> str:
    """键归一化: 去空白/下划线/连字符,小写 — 使「校验通过」「校验_通过」等价"""
    return re.sub(r"[\s_\-]+", "", s).lower()


def _coerce_value(raw: str) -> bool | float | str:
    """字符串 → bool / 数值 / 原始字符串"""
    t = raw.strip()
    if (t.startswith('"') and t.endswith('"')) or (
        t.startswith("'") and t.endswith("'")
    ):
        return t[1:-1]
    if t.lower() in _BOOL_TRUE:
        return True
    if t.lower() in _BOOL_FALSE:
        return False
    try:
        return float(t)
    except ValueError:
        return t


def resolve_key(key: str, context: dict[str, Any]) -> Any:
    """解析上下文键: 精确 → 归一化 → 子串唯一匹配

    Raises:
        ConditionKeyError: 键不存在或子串匹配到多个候选(歧义)
    """
    if key in context:
        return context[key]
    norm = _norm_key(key)
    norm_map = {_norm_key(k): v for k, v in context.items()}
    if norm in norm_map:
        return norm_map[norm]
    # 子串匹配: 键是某上下文键的子串(或反之),要求唯一
    candidates = [
        v for nk, v in norm_map.items() if norm and (norm in nk or nk in norm)
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise ConditionKeyError(key, key)


def evaluate_condition(expr: str, context: dict[str, Any]) -> tuple[bool, str]:
    """求值可读条件表达式,返回 (passed, 求值说明)

    支持形式:
      - 「key == value / != / > / >= / < / <=」(value: bool/数字/带引号字符串)
      - 「key」裸键 → 真值判断
    """
    expr = expr.strip()
    m = _OP_PATTERN.match(expr)
    if m:
        key = m.group("key").strip()
        op = m.group("op")
        right = _coerce_value(m.group("value"))
        left = resolve_key(key, context)
        passed = _compare(left, op, right)
        return passed, f"{key}({left!r}) {op} {right!r} → {passed}"
    b = _BARE_PATTERN.match(expr)
    if b:
        key = b.group("key")
        val = resolve_key(key, context)
        return bool(val), f"{key}({val!r}) 真值 → {bool(val)}"
    raise ConditionKeyError(expr, expr)


def _compare(left: Any, op: str, right: Any) -> bool:
    """安全比较: 双侧数值化时按数值,否则按字符串(忽略大小写)"""
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return _apply_op(left, op, right)
    return _apply_op(_to_str(left).lower(), op, _to_str(right).lower())


def _apply_op(lhs: Any, op: str, rhs: Any) -> bool:
    """在已同型的操作数上应用比较运算"""
    results = {
        "==": lhs == rhs,
        "!=": lhs != rhs,
        ">": lhs > rhs,
        ">=": lhs >= rhs,
        "<": lhs < rhs,
        "<=": lhs <= rhs,
    }
    return bool(results[op])


def _to_str(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


class ConditionRouter:
    """默认边路由: data/control 边恒激活,condition 边按上下文求值

    无条件文本的 condition 边退化为恒真(与 refine 侧「降级 data 边」语义一致)。
    求值遇到未定义键时抛 ConditionKeyError,由引擎转为 ABORTED + 诊断。
    """

    def route(self, edge: Edge, context: dict[str, Any]) -> bool:
        if edge.edge_type != "condition":
            return True
        expr = str(edge.metadata.get("condition", "") or edge.label or "").strip()
        if not expr:
            return True
        passed, _ = evaluate_condition(expr, context)
        return passed


class DefaultNodeExecutor:
    """默认节点执行器 — 模拟执行并发布输出到上下文

    BLOCKED 节点(槽位缺口未填)执行失败 — 接地校验的一部分。
    fail_nodes 用于测试/演练注入确定性失败。
    """

    def __init__(self, fail_nodes: set[str] | None = None) -> None:
        self.fail_nodes = fail_nodes or set()

    def execute(self, node: Node, context: dict[str, Any]) -> dict[str, Any]:
        if node.id in self.fail_nodes:
            raise ExecutionError(f"节点 {node.id} 被配置为模拟失败")
        if node.status == NodeStatus.BLOCKED:
            raise ExecutionError(
                f"节点 {node.id} 处于 blocked 状态(槽位缺口未填),不可执行"
            )
        out: dict[str, Any] = {"executed": True}
        for k, v in node.slots.items():
            out[k] = v
        return out


@dataclass
class ExecutionEngine:
    """DAG 执行引擎

    拓扑序逐节点执行;边的激活状态决定下游是否运行:
      - 入度 0 的节点为入口(L2/L3 无同层边的节点自动成为入口)
      - 任一入边被激活即可运行(分支合流语义)
      - 全部入边未激活 → SKIPPED(分支未命中 / 上游失败 / 不可达)
    """

    executor: NodeExecutor = field(default_factory=DefaultNodeExecutor)
    router: EdgeRouter = field(default_factory=ConditionRouter)
    max_steps: int = 10000
    """执行步数上限,防御病态大图"""

    def run(
        self, graph: NodeGraph, initial_context: dict[str, Any] | None = None
    ) -> ExecutionSummary:
        """执行 DAG,返回 ExecutionSummary(不抛业务异常,结果都在 summary 里)"""
        summary = ExecutionSummary(context=dict(initial_context or {}))
        if not graph.nodes:
            summary.status = ExecutionStatus.ABORTED
            summary.error = "图为空,无可执行节点"
            return summary

        records: dict[str, ExecutionRecord] = {
            nid: ExecutionRecord(node_id=nid, title=n.title)
            for nid, n in graph.nodes.items()
        }
        in_edges: dict[str, list[Edge]] = {}
        out_edges: dict[str, list[Edge]] = {}
        for e in graph.edges:
            out_edges.setdefault(e.source, []).append(e)
            in_edges.setdefault(e.target, []).append(e)
        activated_edges: set[str] = set()

        try:
            order = graph.topological_order()
        except ValueError as exc:
            summary.status = ExecutionStatus.ABORTED
            summary.error = f"图存在环,不可执行: {exc}"
            summary.records = list(records.values())
            self._fill_stats(summary, records)
            return summary

        steps = 0
        abort_error: str | None = None
        for node_id in order:
            rec = records[node_id]
            node = graph.nodes[node_id]
            ins = in_edges.get(node_id, [])
            if ins and not any(e.id in activated_edges for e in ins):
                rec.status = ExecutionStatus.SKIPPED
                rec.detail = "分支未命中 / 上游未激活 / 不可达"
                continue
            steps += 1
            if steps > self.max_steps:
                abort_error = f"超出最大执行步数 {self.max_steps}"
                break
            rec.status = ExecutionStatus.RUNNING
            rec.started_at = _now_iso()
            t0 = time.perf_counter()
            try:
                output = self.executor.execute(node, summary.context)
            except Exception as exc:  # noqa: BLE001 — 任意执行器异常都转为记录
                rec.status = ExecutionStatus.FAILED
                rec.error = (
                    str(exc) if isinstance(exc, ExecutionError)
                    else f"{type(exc).__name__}: {exc}"
                )
                rec.finished_at = _now_iso()
                rec.duration_ms = (time.perf_counter() - t0) * 1000
                continue
            rec.duration_ms = (time.perf_counter() - t0) * 1000
            rec.finished_at = _now_iso()
            rec.status = ExecutionStatus.SUCCESS
            rec.output = output
            self._publish(node, output, summary.context)
            # 求值出边(条件接地失败 → 整图 ABORTED)
            for e in out_edges.get(node_id, []):
                try:
                    if self.router.route(e, summary.context):
                        activated_edges.add(e.id)
                except ConditionKeyError as exc:
                    abort_error = f"边 {e.source} → {e.target} 条件接地失败: {exc}"
                    break
            if abort_error is not None:
                break

        summary.records = list(records.values())
        if abort_error is not None:
            summary.status = ExecutionStatus.ABORTED
            summary.error = abort_error
        else:
            failed = [
                r for r in summary.records if r.status == ExecutionStatus.FAILED
            ]
            if failed:
                summary.status = ExecutionStatus.FAILED
                summary.error = "; ".join(
                    f"{r.node_id}: {r.error}" for r in failed[:5]
                )
            else:
                summary.status = ExecutionStatus.SUCCESS
        self._fill_stats(summary, records)
        return summary

    @staticmethod
    def _publish(
        node: Node, output: dict[str, Any], context: dict[str, Any]
    ) -> None:
        """把节点输出发布到上下文: node.id / 归一化标题 / 扁平 slot 键

        slot 键 setdefault(首个发布者生效): 拓扑序下确定,
        避免下游节点静默覆盖上游发布的同名信号。
        """
        context[node.id] = output
        context[_norm_key(node.title)] = output
        for k in node.slots:
            context.setdefault(k, output[k])

    @staticmethod
    def _fill_stats(
        summary: ExecutionSummary, records: dict[str, ExecutionRecord]
    ) -> None:
        vals = list(records.values())
        executed = sum(1 for r in vals if r.status == ExecutionStatus.SUCCESS)
        skipped = sum(1 for r in vals if r.status == ExecutionStatus.SKIPPED)
        failed = sum(1 for r in vals if r.status == ExecutionStatus.FAILED)
        summary.stats = {
            "total": len(vals),
            "executed": executed,
            "skipped": skipped,
            "failed": failed,
            # ABORTED 时未触达的节点(= total - 已处理),保证统计自洽
            "pending": len(vals) - executed - skipped - failed,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
