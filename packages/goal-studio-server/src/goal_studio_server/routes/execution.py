"""执行引擎 + 数据飞轮 REST API(方案 #1/#2 的服务化入口)

Session 域(挂 /api/sessions/{session_id}):
  POST /execute            — 执行会话 DAG(grounded validation),记录入会话
  GET  /execution/records  — 取最近一次执行的逐节点记录

全局域(挂 /api/flywheel):
  GET  /stats   — 样本统计
  POST /train   — 训练梯度提升树,返回指标 + 特征重要性
  POST /predict — 预测补丁 KEEP/REVERT 概率(咨询式,不门控)
"""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from goal_studio.core.contract import Patch
from goal_studio.core.execution import DefaultNodeExecutor, ExecutionEngine
from goal_studio.core.ratchet_data import RefineRecord, build_feature_vector
from goal_studio.core.session_manager import Session, SessionManager
from pydantic import BaseModel

from goal_studio_server.deps import (
    get_adapter_registry,
    get_ratchet_store,
    get_session_manager,
)
from goal_studio_server.metrics import METRICS

router = APIRouter()
flywheel_router = APIRouter()


# === 响应/请求模型 ===

class ExecuteRequest(BaseModel):
    """执行请求: 初始上下文(触发变量)+ 可选注入失败节点(演练)"""

    context: dict[str, Any] = {}
    fail_nodes: list[str] = []


class ExecutionRecordModel(BaseModel):
    node_id: str
    title: str
    status: str
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    output: dict[str, Any] = {}
    error: str | None = None
    detail: str = ""


class ExecutionResponse(BaseModel):
    status: str
    passed: bool
    error: str | None = None
    stats: dict[str, int] = {}
    records: list[ExecutionRecordModel] = []
    phase: str = ""


class FlywheelStatsResponse(BaseModel):
    total: int
    keep: int
    revert: int
    early_stop: int
    other: int
    db_path: str = ""


class TrainResponse(BaseModel):
    trained: bool
    metrics: dict[str, Any] = {}
    model_path: str = ""
    error: str = ""


class PredictRequest(BaseModel):
    """预测请求: 补丁前/后图 + 补丁本体(new_score 未知,以 old_score 占位)"""

    graph_before: dict[str, Any]
    graph_after: dict[str, Any]
    patch: dict[str, Any]
    old_score: float = 0.0


class PredictResponse(BaseModel):
    prob_keep: float
    decision: str
    trained_at: str = ""
    advisory: str = ""


# === 辅助 ===

def _get_session(mgr: SessionManager, session_id: str) -> Session:
    try:
        return mgr.get(session_id)
    except (KeyError, ValueError):
        raise HTTPException(404, f"Session {session_id} not found") from None


def _model_path() -> Path:
    import os

    return Path(
        os.getenv("GOAL_STUDIO_FLYWHEEL_MODEL", "")
        or Path.home() / ".goal-studio" / "patch_classifier.pkl"
    )


def patch_to_dict(patch: Patch) -> dict[str, Any]:
    """Patch(核心 dataclass)→ 可 JSON 化 dict"""
    return {
        "op": patch.op.value,
        "target_node_id": patch.target_node_id,
        "target_node_id_2": patch.target_node_id_2,
        "slots": patch.slots,
        "new_node": patch.new_node,
        "position": patch.position,
        "source": patch.source,
        "rationale": patch.rationale,
    }


def record_refine_outcome(
    session_id: str,
    s: Session,
    graph_before: dict[str, Any],
    graph_after: dict[str, Any],
    patch: Patch | dict[str, Any],
    decision: str,
    old_score: float,
    new_score: float,
) -> None:
    """数据飞轮写入钩子(由 /refine 调用,best-effort 不阻断主流程)"""
    patch_dict = (
        patch_to_dict(patch) if isinstance(patch, Patch) else dict(patch)
    )
    store = get_ratchet_store()
    store.save_refine_record(
        RefineRecord(
            session_id=session_id,
            goal=s.goal.goal if s.goal else "",
            graph_before=graph_before,
            graph_after=graph_after,
            patch=patch_dict,
            old_score=old_score,
            new_score=new_score,
            decision=decision,
        )
    )


# === 执行引擎端点(Session 域) ===

@router.post("/execute", response_model=ExecutionResponse)
async def execute_workflow(
    session_id: str,
    req: ExecuteRequest,
    mgr: SessionManager = Depends(get_session_manager),
    registry: Any = Depends(get_adapter_registry),
) -> ExecutionResponse:
    """执行会话 DAG — grounded validation: 图必须「跑得通」

    执行器/路由器优先取适配器注入(get_executor/get_router),
    默认 DefaultNodeExecutor(模拟执行)+ ConditionRouter(条件求值)。
    """
    s = _get_session(mgr, session_id)
    if not s.graph or not s.goal:
        raise HTTPException(400, "Session has no graph/goal, call /draft first")

    adapter = registry.get_for_goal(s.goal)
    executor = adapter.get_executor() or DefaultNodeExecutor(
        fail_nodes=set(req.fail_nodes)
    )
    edge_router = adapter.get_router()
    engine = (
        ExecutionEngine(executor=executor)
        if edge_router is None
        else ExecutionEngine(executor=executor, router=edge_router)
    )

    loop = asyncio.get_running_loop()
    METRICS["llm_calls_total"] += 1
    try:
        summary = await loop.run_in_executor(
            None, partial(engine.run, s.graph, req.context)
        )
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise

    # 记录入会话(最近一次 + 历史上限 20 次),per-session 锁保护
    with mgr.session_lock(session_id):
        history = s.metadata.setdefault("execution_history", [])
        history.append(summary.to_dict())
        if len(history) > 20:
            del history[:-20]
        s.metadata["last_execution"] = summary.to_dict()
        s.add_dialog(
            "system",
            f"DAG 执行: {summary.status.value}"
            f" (执行 {summary.stats.get('executed', 0)}/"
            f"{summary.stats.get('total', 0)},跳过 {summary.stats.get('skipped', 0)})",
        )
        phase = s.phase.value
        mgr.save(s)

    return ExecutionResponse(
        status=summary.status.value,
        passed=summary.passed,
        error=summary.error,
        stats=summary.stats,
        records=[ExecutionRecordModel(**r.to_dict()) for r in summary.records],
        phase=phase,
    )


@router.get("/execution/records", response_model=list[ExecutionRecordModel])
async def get_execution_records(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> list[ExecutionRecordModel]:
    """取最近一次执行的逐节点记录"""
    s = _get_session(mgr, session_id)
    last = s.metadata.get("last_execution")
    if not last:
        raise HTTPException(404, "No execution records, call /execute first")
    records = last.get("records", []) if isinstance(last, dict) else []
    return [ExecutionRecordModel(**r) for r in records]


# === 数据飞轮端点(全局域) ===

@flywheel_router.get("/stats", response_model=FlywheelStatsResponse)
async def flywheel_stats() -> FlywheelStatsResponse:
    """样本统计: 总量 + KEEP/REVERT/EARLY_STOP 分布"""
    stats = get_ratchet_store().stats()
    return FlywheelStatsResponse(**stats)


@flywheel_router.post("/train", response_model=TrainResponse)
async def flywheel_train() -> TrainResponse:
    """训练梯度提升树(sklearn 可选;样本不足时返回明确指引)"""
    from goal_studio.core.training import PatchClassifier

    store = get_ratchet_store()
    X, y = store.get_training_data()  # noqa: N806 — ML 惯例(X/y)
    clf = PatchClassifier()
    try:
        metrics = clf.train(X, y)
    except RuntimeError as exc:
        return TrainResponse(trained=False, error=str(exc))
    model_path = clf.save(_model_path())
    return TrainResponse(
        trained=True, metrics=metrics, model_path=model_path
    )


@flywheel_router.post("/predict", response_model=PredictResponse)
async def flywheel_predict(req: PredictRequest) -> PredictResponse:
    """预测补丁 KEEP 概率(咨询式参考,不参与棘轮门控)"""
    from goal_studio.core.training import NotTrainedError, PatchClassifier

    features = build_feature_vector(
        RefineRecord(
            session_id="predict",
            goal="",
            graph_before=req.graph_before,
            graph_after=req.graph_after,
            patch=req.patch,
            old_score=req.old_score,
            new_score=req.old_score,  # 未知,占位(分数特征不参与预测语义)
            decision="keep",
        )
    )
    try:
        clf = PatchClassifier.load(_model_path())
        prob, decision = clf.predict(features)
    except NotTrainedError as exc:
        raise HTTPException(400, str(exc)) from None
    return PredictResponse(
        prob_keep=prob,
        decision=decision,
        trained_at=clf.trained_at,
        advisory=f"咨询式预测: {decision} (p={prob:.2f}),不参与棘轮门控",
    )
