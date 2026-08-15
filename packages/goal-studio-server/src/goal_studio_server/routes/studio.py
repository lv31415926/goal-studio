"""创作循环 REST API: draft / refine / validate / build + 棘轮门控
"""
from __future__ import annotations

import asyncio
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from goal_studio.core.adapter_registry import AdapterRegistry
from goal_studio.core.contract import Artifact, GoalContract, Patch, PatchOp, ProductType
from goal_studio.core.node_graph import NodeGraph
from goal_studio.core.ratchet_gate import RatchetDecision
from goal_studio.core.session_manager import Session, SessionManager, SessionPhase
from pydantic import BaseModel

from goal_studio_server.deps import (
    get_adapter_registry,
    get_artifact_store,
    get_session_manager,
)
from goal_studio_server.metrics import METRICS
from goal_studio_server.models import (
    ArtifactModel,
    DraftRequest,
    GoalContractModel,
    NodeGraphModel,
    PatchModel,
    RefineRequest,
    ReportModel,
)
from goal_studio_server.models import (
    ProductType as ModelProductType,
)

router = APIRouter()


# === 棘轮门控响应模型 ===

class RatchetResponse(BaseModel):
    """棘轮门控响应"""
    decision: str
    old_score: float
    new_score: float
    delta: float
    best_score: float
    current_score: float
    reverted: bool = False
    early_stopped: bool = False
    message: str = ""
    graph: NodeGraphModel | None = None


class RatchetHistoryItem(BaseModel):
    round: int
    old_score: float
    new_score: float
    delta: float
    decision: str
    patch_summary: str = ""
    issues: list[str] = []


class RatchetHistoryResponse(BaseModel):
    history: list[RatchetHistoryItem]
    best_score: float
    current_score: float
    snapshot_count: int


# === 辅助函数 ===

# PR-ROUND-4/A-05: IDOR 防护 — 当前单 token 共享模式未传 owner_id,多用户时需传入
def _get_session(mgr: SessionManager, session_id: str) -> Session:
    try:
        return mgr.get(session_id)
    except (KeyError, ValueError):
        # PR-ROUND-1/A-04: 非法 session_id 格式(ValueError)同样返回 404
        raise HTTPException(404, f"Session {session_id} not found") from None


def _to_goal_contract(g: GoalContractModel | GoalContract) -> GoalContract:
    return GoalContract(
        goal=g.goal,
        prerequisites=g.prerequisites,
        constraints=g.constraints,
        success_criteria=g.success_criteria,
        product_type=ProductType(g.product_type),
        metadata=g.metadata,
    )


def _to_patch(p: PatchModel) -> Patch:
    return Patch(
        op=PatchOp(p.op),
        target_node_id=p.target_node_id,
        target_node_id_2=p.target_node_id_2,
        slots=p.slots,
        new_node=p.new_node,
        position=p.position,
        source=p.source,
        rationale=p.rationale,
    )


def _patch_summary(patch: Patch) -> str:
    """生成 patch 摘要"""
    parts = [patch.op.value]
    if patch.target_node_id:
        parts.append(patch.target_node_id)
    if patch.slots:
        slot_str = ", ".join(f"{k}={v}" for k, v in list(patch.slots.items())[:3])
        parts.append(slot_str)
    return " ".join(parts)


# === 核心 API ===

@router.post("/draft", response_model=NodeGraphModel)
async def draft(
    session_id: str,
    req: DraftRequest,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> NodeGraphModel:
    """起草 DAG — 调用 Adapter.draft()"""
    s = _get_session(mgr, session_id)
    goal = _to_goal_contract(req.goal)
    adapter = registry.get_for_goal(goal)
    # PR-ROUND-1/B-06: goal 赋值 + draft + 图更新 + 保存整体持 per-session 锁,
    # 避免与并发 /refine(已持锁) 竞争修改同一 session
    loop = asyncio.get_running_loop()

    # P1-3: 使用公共 session_lock CM(含 session_id 校验),不再访问私有 _get_session_lock
    def _draft_with_lock():
        with mgr.session_lock(session_id):
            s.goal = goal
            graph = adapter.draft(goal)
            s.update_graph(graph)
            s.phase = SessionPhase.VISUALIZE
            mgr.save(s)
            return graph

    METRICS["llm_calls_total"] += 1
    try:
        graph = await loop.run_in_executor(None, _draft_with_lock)
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise
    return NodeGraphModel(**graph.to_dict())


@router.post("/refine", response_model=RatchetResponse)
async def refine(
    session_id: str,
    req: RefineRequest,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> RatchetResponse:
    """精化补丁 — 自动验证 + 棘轮门控

    流程:
      1. 保存快照 (refine 前的 graph + score)
      2. adapter.refine() 生成 new_graph
      3. adapter.validate(new_graph) 得到 new_score
      4. ratchet.judge(old_score, new_score) → decision
      5. KEEP: 保留 new_graph; REVERT: 恢复快照
    """
    s = _get_session(mgr, session_id)
    if not s.graph or not s.goal:
        raise HTTPException(400, "Session has no graph/goal, call /draft first")
    # R3-07: 会话状态机 phase 合法转换校验(refine 允许从 VISUALIZE/REFINE/VERIFY 进入)
    if s.phase not in (SessionPhase.VISUALIZE, SessionPhase.REFINE, SessionPhase.VERIFY):
        raise HTTPException(409, f"Cannot refine in phase {s.phase.value}")

    adapter = registry.get_for_goal(s.goal)
    patch = _to_patch(req.patch)
    patch_sum = _patch_summary(patch)

    # 1. 保存快照
    old_score = s.current_score

    # PR-M: 同步 LLM 调用 + 棘轮判定 + 状态更新整体移入线程池并持 per-session 锁,
    # 保证 refine 全流程原子性(避免并发 refine 互相覆盖快照/分数)
    # R3-08: 统一使用 asyncio.get_running_loop()
    loop = asyncio.get_running_loop()

    # P1-3: 使用公共 session_lock CM
    def _refine_with_lock():
        with mgr.session_lock(session_id):
            s.save_snapshot(patch_summary=patch_sum)
            new_graph = adapter.refine(s.graph, patch)
            report = adapter.validate(new_graph)
            new_score = report.score
            decision = s.ratchet.judge(
                old_score=old_score,
                new_score=new_score,
                current_graph=new_graph,
                patch_summary=patch_sum,
            )
            final_graph = s.apply_ratchet_decision(decision, new_graph, new_score)
            s.phase = SessionPhase.REFINE
            s.add_dialog("user", f"{patch.op.value}: {patch.target_node_id or ''} {patch.slots}")
            # R3-09: dialog role 统一为 assistant(与 WS 一致)
            s.add_dialog(
                "assistant", f"棘轮判定: {decision.value} (Δ={new_score - old_score:+.1f})"
            )
            mgr.save(s)
            return decision, final_graph, new_score

    METRICS["llm_calls_total"] += 1
    try:
        decision, final_graph, new_score = await loop.run_in_executor(
            None, _refine_with_lock
        )
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise

    # 构造响应
    messages = {
        RatchetDecision.KEEP: f"保留修改 (分数 {old_score:.1f} → {new_score:.1f})",
        RatchetDecision.REVERT: f"回滚修改 (分数下降 {old_score:.1f} → {new_score:.1f})",
        RatchetDecision.EARLY_STOP: f"早停 (连续2轮 Δ<{s.ratchet.early_stop_delta}),恢复最佳版本",
        RatchetDecision.ARBITRATE: "双评审分差过大,需仲裁",
    }

    return RatchetResponse(
        decision=decision.value,
        old_score=old_score,
        new_score=new_score,
        delta=new_score - old_score,
        best_score=s.ratchet.best_score,
        current_score=s.current_score,
        reverted=decision == RatchetDecision.REVERT,
        early_stopped=decision == RatchetDecision.EARLY_STOP,
        message=messages.get(decision, ""),
        graph=NodeGraphModel(**final_graph.to_dict()),
    )


@router.post("/validate", response_model=ReportModel)
async def validate(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> ReportModel:
    """验证 — 调用 Adapter.validate() + 更新棘轮状态"""
    s = _get_session(mgr, session_id)
    if not s.graph or not s.goal:
        raise HTTPException(400, "Session has no graph/goal")
    # R3-07: 会话状态机 phase 合法转换校验(validate 允许从 VISUALIZE/REFINE/VERIFY 进入)
    if s.phase not in (SessionPhase.VISUALIZE, SessionPhase.REFINE, SessionPhase.VERIFY):
        raise HTTPException(409, f"Cannot validate in phase {s.phase.value}")
    adapter = registry.get_for_goal(s.goal)
    # PR-M: 同步 LLM 调用移入线程池,避免阻塞事件循环
    # R3-08: 统一使用 asyncio.get_running_loop()
    loop = asyncio.get_running_loop()
    METRICS["llm_calls_total"] += 1
    try:
        report = await loop.run_in_executor(None, partial(adapter.validate, s.graph))
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改同一 session
    with mgr.session_lock(session_id):
        s.phase = SessionPhase.VERIFY
        s.current_score = report.score
        # 更新 best (如果当前分数更高)
        if report.score > s.ratchet.best_score:
            s.ratchet.best_score = report.score
            # PR-ROUND-2/C-03: 深拷贝,避免后续 s.graph 修改污染 best_graph
            s.ratchet.best_graph = NodeGraph.from_dict(s.graph.to_dict())
            s.best_score = report.score
            s.best_graph = NodeGraph.from_dict(s.graph.to_dict())
        s.ratchet_history = [r.to_dict() for r in s.ratchet.history]
        mgr.save(s)
    return ReportModel(
        passed=report.passed,
        score=report.score,
        dimensions=report.dimensions,
        issues=report.issues,
        suggestions=report.suggestions,
        evaluator=report.evaluator,
    )


@router.post("/build", response_model=ArtifactModel)
async def build(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> ArtifactModel:
    """构建产物 — 使用 best_graph (历史最佳) 而非 current graph

    保证最终产物质量是历史最高分的版本。
    """
    s = _get_session(mgr, session_id)
    if not s.graph or not s.goal:
        raise HTTPException(400, "Session has no graph/goal")
    # R3-07: 会话状态机 phase 合法转换校验(build 允许从 VERIFY/BUILD 进入)
    if s.phase not in (SessionPhase.VERIFY, SessionPhase.BUILD):
        raise HTTPException(409, f"Cannot build in phase {s.phase.value}")
    adapter = registry.get_for_goal(s.goal)
    # 优先使用 best_graph,回退到 current graph
    # PR-ROUND-2/C-04: 传深拷贝给 adapter,避免 adapter 修改污染 session 状态
    source_graph = s.ratchet.best_graph or s.graph
    build_graph = NodeGraph.from_dict(source_graph.to_dict())
    # PR-M: 同步 LLM 调用移入线程池,避免阻塞事件循环
    loop = asyncio.get_running_loop()
    METRICS["llm_calls_total"] += 1
    try:
        artifact: Artifact = await loop.run_in_executor(None, partial(adapter.build, build_graph))
    except Exception:
        METRICS["llm_errors_total"] += 1
        raise
    # PR-S3: 产物持久化 + 记录 best_artifact_id,供删除会话时级联清理
    store = get_artifact_store()
    artifact_id = await loop.run_in_executor(
        None, partial(store.save, artifact, f"session={session_id}")
    )
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改同一 session
    with mgr.session_lock(session_id):
        s.phase = SessionPhase.BUILD
        s.best_artifact_id = artifact_id
        if not s.metadata.get("artifact_ids"):
            s.metadata["artifact_ids"] = []
        if artifact_id not in s.metadata["artifact_ids"]:
            s.metadata["artifact_ids"].append(artifact_id)
        mgr.save(s)
    return ArtifactModel(
        product_type=ModelProductType(artifact.product_type),
        content=artifact.content,
        files=artifact.files,
        metadata=artifact.metadata,
        graph_snapshot=artifact.graph_snapshot,
        version=artifact.version,
    )


# === 棘轮门控专用端点 ===

@router.post("/ratchet/undo", response_model=NodeGraphModel)
async def ratchet_undo(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> NodeGraphModel:
    """撤销最近一次 refine — 恢复到上一个快照"""
    s = _get_session(mgr, session_id)
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改
    with mgr.session_lock(session_id):
        restored = s.undo()
        if restored is None:
            raise HTTPException(400, "No snapshot to undo")
        s.add_dialog("system", "用户撤销最近一次修改")
        mgr.save(s)
    return NodeGraphModel(**restored.to_dict())


@router.get("/ratchet/history", response_model=RatchetHistoryResponse)
async def ratchet_history(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> RatchetHistoryResponse:
    """获取棘轮门控历史"""
    s = _get_session(mgr, session_id)
    return RatchetHistoryResponse(
        history=[
            RatchetHistoryItem(**r.to_dict())
            for r in s.ratchet.history
        ],
        best_score=s.ratchet.best_score,
        current_score=s.current_score,
        snapshot_count=len(s.ratchet.snapshots),
    )


@router.post("/ratchet/restore_best", response_model=NodeGraphModel)
async def ratchet_restore_best(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> NodeGraphModel:
    """恢复到历史最佳版本"""
    s = _get_session(mgr, session_id)
    # R3-06: 状态更新纳入 per-session 锁,避免并发修改
    with mgr.session_lock(session_id):
        restored = s.restore_best()
        if restored is None:
            raise HTTPException(400, "No best graph to restore")
        s.add_dialog("system", f"恢复到最佳版本 (分数 {s.ratchet.best_score:.1f})")
        mgr.save(s)
    return NodeGraphModel(**restored.to_dict())
