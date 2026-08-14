"""会话管理 REST API"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from goal_studio.core.session_manager import Session, SessionManager

from goal_studio_server.deps import get_artifact_store, get_session_manager
from goal_studio_server.models import (
    CreateSessionRequest,
    GoalContractModel,
    SessionModel,
    SessionSummary,
)
# R3-10: 导入活跃连接注册表,删除会话时主动关闭
from goal_studio_server.routes.ws import _session_connections, cleanup_session
from goal_studio_server.ws_protocol import msg_error

_logger = logging.getLogger(__name__)

router = APIRouter()

# PR-ROUND-4/A-05: IDOR 防护说明
# SessionManager.get()/delete() 支持 owner_id 参数进行所有权校验(防 IDOR),
# 但当前架构为单 API Token 共享模式,所有用户使用同一 token,owner_id 未在路由层传入。
# 引入多用户体系时,需从认证上下文提取 user_id 并传入 mgr.get(session_id, owner_id=user_id)。


def _session_to_model(s: Session) -> SessionModel:
    """Session → SessionModel"""
    g = None
    if s.goal:
        g = GoalContractModel(
            goal=s.goal.goal,
            prerequisites=s.goal.prerequisites,
            constraints=s.goal.constraints,
            success_criteria=s.goal.success_criteria,
            product_type=s.goal.product_type,
            metadata=s.goal.metadata,
        )
    return SessionModel(
        id=s.id,
        name=s.name,
        goal=g,
        graph=None,  # 由 studio 路由单独获取
        phase=s.phase.value,
        best_score=s.best_score,
        current_score=s.current_score,
        ratchet_history=s.ratchet_history,
        dialog=s.dialog,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
        # PR-ROUND-3/D-03: 返回 best_artifact_id
        best_artifact_id=getattr(s, "best_artifact_id", None),
        metadata=s.metadata,
    )


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    mgr: SessionManager = Depends(get_session_manager),
) -> list[SessionSummary]:
    """列出所有会话(PR-ROUND-3/D-05: 支持分页)

    Args:
        limit: 返回数量上限(1-200,默认 50)
        offset: 偏移量(默认 0)
    """
    # PR-ROUND-3/D-05: 分页参数校验
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    sessions = mgr.list_sessions()
    # 分页切片
    paged = sessions[offset:offset + limit]
    _logger.info(
        "list_sessions",
        extra={"total": len(sessions), "returned": len(paged), "offset": offset, "limit": limit},
    )
    return [SessionSummary(**s) for s in paged]


# PR-ROUND-4/A-05: IDOR 防护 — create 无需 owner_id 校验(新会话由当前认证用户创建)
@router.post("", response_model=SessionModel)
async def create_session(
    req: CreateSessionRequest,
    mgr: SessionManager = Depends(get_session_manager),
) -> SessionModel:
    """创建新会话"""
    from goal_studio.core.contract import GoalContract, ProductType

    goal = None
    if req.goal:
        goal = GoalContract(
            goal=req.goal.goal,
            prerequisites=req.goal.prerequisites,
            constraints=req.goal.constraints,
            success_criteria=req.goal.success_criteria,
            product_type=ProductType(req.goal.product_type),
            metadata=req.goal.metadata,
        )
    s = mgr.create(name=req.name, goal=goal)
    _logger.info("session_created", extra={"session_id": s.id, "session_name": req.name})
    return _session_to_model(s)


# PR-ROUND-4/A-05: IDOR 防护 — 当前单 token 模式下未传 owner_id,多用户时需传入
@router.get("/{session_id}", response_model=SessionModel)
async def get_session(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> SessionModel:
    """获取会话详情(含图)"""
    try:
        s = mgr.get(session_id)
    except (KeyError, ValueError):
        # PR-ROUND-1/A-04: ValueError 来自 _validate_session_id(非法 session_id 格式),
        # 同样返回 404 而非泄漏为 500(避免日志噪音和监控误报)
        _logger.warning("session_not_found", extra={"session_id": session_id})
        raise HTTPException(404, f"Session {session_id} not found") from None
    m = _session_to_model(s)
    if s.graph:
        from goal_studio_server.models import NodeGraphModel
        m.graph = NodeGraphModel(**s.graph.to_dict())
    return m


# PR-ROUND-4/A-05: IDOR 防护 — 当前单 token 模式下未传 owner_id,多用户时需传入
@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    mgr: SessionManager = Depends(get_session_manager),
) -> None:
    """删除会话(PR-S3: 级联清理该会话的产物文件)

    R3-10: 删除前主动关闭该会话的活跃 WS 连接(发送 session_deleted 后 close)。
    R3-11: 返回 204 无 body。
    """
    # R3-10: 删除前通知并关闭活跃 WS 连接,避免僵尸连接继续操作已删除会话
    await _close_active_ws_connections(session_id)
    # PR-S3: 删除前先读取会话元数据中的 artifact_ids(删除后无法读取)
    artifact_ids = _collect_artifact_ids(mgr, session_id)
    ok = mgr.delete(session_id)
    if not ok:
        _logger.warning("delete_session_not_found", extra={"session_id": session_id})
        raise HTTPException(404, f"Session {session_id} not found")
    cleanup_session(session_id)
    # PR-S3: 删除会话时清理其产物(避免孤儿 artifact 残留)
    _cleanup_artifacts(session_id, artifact_ids)
    _logger.info("session_deleted", extra={"session_id": session_id})
    return None


async def _close_active_ws_connections(session_id: str) -> None:
    """R3-10: 主动关闭该会话的所有活跃 WS 连接"""
    conns = _session_connections.get(session_id)
    if not conns:
        return
    for ws_conn in list(conns):
        try:
            await ws_conn.send_json(
                msg_error(session_id, "session deleted", "session_deleted").model_dump()
            )
            await ws_conn.close(code=1000)
        except Exception:  # noqa: BLE001
            pass


def _collect_artifact_ids(mgr: SessionManager, session_id: str) -> list[str]:
    """删除前收集会话关联的产物 ID(PR-S3)"""
    try:
        s = mgr.get(session_id)
    except (KeyError, ValueError):
        return []
    artifact_ids = list(s.metadata.get("artifact_ids", []))
    if s.best_artifact_id and s.best_artifact_id not in artifact_ids:
        artifact_ids.append(s.best_artifact_id)
    return artifact_ids


def _cleanup_artifacts(session_id: str, artifact_ids: list[str]) -> None:
    """从 ArtifactStore 删除会话关联的产物(PR-S3)"""
    if not artifact_ids:
        return
    store = get_artifact_store()
    removed: list[str] = []
    for aid in artifact_ids:
        try:
            if store.delete(aid):
                removed.append(aid)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "artifact_cleanup_failed",
                extra={"session_id": session_id, "artifact_id": aid},
            )
    if removed:
        _logger.info(
            "session_artifacts_removed",
            extra={"session_id": session_id, "removed": removed},
        )
