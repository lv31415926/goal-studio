"""SessionManager: 多会话隔离 + 状态恢复

每个创作任务一个 Session,保存:
  - GoalContract
  - 当前 NodeGraph
  - RatchetGate 历史
  - 对话上下文
支持中断恢复、多会话切换。
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    import fcntl
except ImportError:            # Windows 无 fcntl
    fcntl: Any = None  # type: ignore[no-redef]

from goal_studio.core.contract import GoalContract
from goal_studio.core.node_graph import NodeGraph
from goal_studio.core.ratchet_gate import RatchetDecision, RatchetGate

# PR-G/G-1: session_id 白名单 - uuid4().hex[:12] 格式(12 位小写十六进制)
_SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")


def _validate_session_id(session_id: str) -> str:
    """校验 session_id 格式,防止路径遍历攻击"""
    if not session_id or not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(f"Invalid session_id: {session_id}")
    return session_id


class SessionPhase(str, Enum):
    """会话阶段"""

    INIT = "init"
    DRAFT = "draft"
    VISUALIZE = "visualize"
    REFINE = "refine"
    VERIFY = "verify"
    BUILD = "build"
    DONE = "done"
    PAUSED = "paused"


@dataclass
class Session:
    """创作会话"""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    """会话唯一 ID"""

    name: str = ""
    """会话名称(用户可读)"""

    goal: GoalContract | None = None
    """目标契约"""

    graph: NodeGraph | None = None
    """当前 NodeGraph"""

    phase: SessionPhase = SessionPhase.INIT
    """当前阶段"""

    best_score: float = 0.0
    """历史最高分(从 ratchet 同步)"""

    best_graph: NodeGraph | None = None
    """历史最佳图(从 ratchet 同步)"""

    current_score: float = 0.0
    """当前图分数"""

    best_artifact_id: str | None = None
    """最佳产物 ID(build 命令产出,供 install 命令使用)"""

    ratchet: RatchetGate = field(default_factory=RatchetGate)
    """棘轮门控实例"""

    ratchet_history: list[dict[str, Any]] = field(default_factory=list)
    """棘轮判定历史(从 ratchet 同步,用于序列化)"""

    dialog: list[dict[str, str]] = field(default_factory=list)
    """对话历史(供 IntentParser 上下文)"""

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # PR-O: Session IDOR 防护 — owner_id 标识会话所有者(空字符串兼容旧数据)
    owner_id: str = ""

    metadata: dict[str, Any] = field(default_factory=dict)

    def add_dialog(self, role: str, content: str) -> None:
        """添加对话

        PR-ROUND-2/C-02: 限制 dialog 最多 200 条,旧消息自动截断,
        防止长会话 dialog 无限增长导致序列化/反序列化性能退化。
        """
        self.dialog.append({"role": role, "content": content})
        # C-02: 保留最近 200 条对话
        _max_dialog = 200
        if len(self.dialog) > _max_dialog:
            self.dialog = self.dialog[-_max_dialog:]
        self.updated_at = datetime.now(UTC)

    def update_graph(self, graph: NodeGraph) -> None:
        """更新当前图"""
        self.graph = graph
        self.updated_at = datetime.now(UTC)

    def save_snapshot(self, patch_summary: str = "") -> None:
        """refine 前保存快照"""
        if self.graph:
            self.ratchet.save_snapshot(
                graph=self.graph,
                score=self.current_score,
                patch_summary=patch_summary,
            )

    def apply_ratchet_decision(
        self,
        decision: RatchetDecision,
        new_graph: NodeGraph,
        new_score: float,
    ) -> NodeGraph:
        """应用棘轮决策

        KEEP: 保留 new_graph,更新 best
        REVERT: 恢复快照(返回 previous graph)
        EARLY_STOP: 返回 best_graph
        ARBITRATE: 需外部仲裁后再次调用

        Returns:
            最终应保留的 graph
        """
        if decision == RatchetDecision.KEEP:
            self.graph = new_graph
            self.current_score = new_score
            self.best_score = self.ratchet.best_score
            self.best_graph = self.ratchet.best_graph
        elif decision == RatchetDecision.REVERT:
            # 恢复快照
            snapshot = self.ratchet.get_last_snapshot()
            if snapshot:
                self.graph = snapshot.graph
                self.current_score = snapshot.score
        elif decision == RatchetDecision.EARLY_STOP:
            # 恢复最佳
            best = self.ratchet.restore_best()
            if best:
                self.graph = best
                self.current_score = self.ratchet.best_score
                self.best_graph = best
        elif decision == RatchetDecision.ARBITRATE:
            # ARBITRATE: graph 不变,等待外部仲裁后再次调用
            # current_score 已被 judge_with_peers 设为平均分,此处同步
            self.current_score = self.ratchet.current_score

        # 同步 ratchet_history
        self.ratchet_history = [r.to_dict() for r in self.ratchet.history]
        self.updated_at = datetime.now(UTC)
        assert self.graph is not None
        assert isinstance(self.graph, NodeGraph)
        return self.graph

    def undo(self) -> NodeGraph | None:
        """撤销最近一次 refine"""
        snapshot = self.ratchet.undo()
        if snapshot:
            self.graph = snapshot.graph
            self.current_score = snapshot.score
            self.updated_at = datetime.now(UTC)
            return self.graph
        return None

    def restore_best(self) -> NodeGraph | None:
        """恢复到历史最佳版本"""
        best = self.ratchet.restore_best()
        if best:
            self.graph = best
            self.current_score = self.ratchet.best_score
            self.best_graph = best
            self.best_score = self.ratchet.best_score
            self.updated_at = datetime.now(UTC)
            return self.graph
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "goal": _goal_to_dict(self.goal) if self.goal else None,
            "graph": self.graph.to_dict() if self.graph else None,
            "phase": self.phase.value,
            "best_score": self.best_score,
            "current_score": self.current_score,
            "best_artifact_id": self.best_artifact_id,
            "best_graph": self.best_graph.to_dict() if self.best_graph else None,
            "ratchet_history": self.ratchet_history,
            "ratchet_state": self.ratchet.to_dict(),
            "dialog": self.dialog,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "owner_id": self.owner_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        # R1-01: from_dict 中校验 id 格式,防止持久化文件含恶意 ID
        _validate_session_id(data["id"])
        s = cls(
            id=data["id"],
            name=data.get("name", ""),
            phase=SessionPhase(data.get("phase", "init")),
            best_score=data.get("best_score", 0.0),
            current_score=data.get("current_score", 0.0),
            best_artifact_id=data.get("best_artifact_id"),
            ratchet_history=data.get("ratchet_history", []),
            dialog=data.get("dialog", []),
            owner_id=data.get("owner_id", ""),
            metadata=data.get("metadata", {}),
        )
        if data.get("created_at"):
            s.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("updated_at"):
            s.updated_at = datetime.fromisoformat(data["updated_at"])
        if data.get("goal"):
            s.goal = _goal_from_dict(data["goal"])
        if data.get("graph"):
            s.graph = NodeGraph.from_dict(data["graph"])
        if data.get("best_graph"):
            s.best_graph = NodeGraph.from_dict(data["best_graph"])
        # P-LOW-2-3: 用 RatchetGate.from_dict 恢复完整 history(供 UI 展示)
        ratchet_state = data.get("ratchet_state")
        if ratchet_state:
            # 替换为 from_dict 重建的 ratchet,保留 history 记录
            from goal_studio.core.ratchet_gate import RatchetGate
            s.ratchet = RatchetGate.from_dict(ratchet_state)
            # best_graph 单独恢复(snapshots 不重建,can_undo=False)
            if s.best_graph:
                s.ratchet.best_graph = s.best_graph
        return s


def _goal_to_dict(goal: GoalContract) -> dict[str, Any]:
    return {
        "goal": goal.goal,
        "prerequisites": goal.prerequisites,
        "constraints": goal.constraints,
        "success_criteria": goal.success_criteria,
        "product_type": goal.product_type.value,
        "metadata": goal.metadata,
    }


def _goal_from_dict(data: dict[str, Any]) -> GoalContract:
    from goal_studio.core.contract import ProductType

    return GoalContract(
        goal=data["goal"],
        prerequisites=data.get("prerequisites", []),
        constraints=data.get("constraints", []),
        success_criteria=data.get("success_criteria", []),
        product_type=ProductType(data.get("product_type", "skill")),
        metadata=data.get("metadata", {}),
    )


_logger = logging.getLogger(__name__)


class SessionManager:
    """会话管理器"""

    # P-LOW-2-4: LRU 缓存最大容量
    _CACHE_MAX = 64

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # P-LOW-2-4: OrderedDict LRU + RLock(因 get 内部会更新缓存,用可重入锁)
        self._cache: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.RLock()
        # PR-M: per-session 内存锁 — 保护内存状态并发修改(get 返回引用,需调用方在锁内完成修改)
        self._session_locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()  # 保护 _session_locks 字典本身

    def _get_session_lock(self, session_id: str) -> threading.RLock:
        """获取 per-session 可重入锁(PR-M)

        不同 session 互不阻塞,同 session 串行化内存修改。
        使用 RLock 避免同线程重入死锁(get 内部调用 save 等场景)。
        """
        with self._locks_guard:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = threading.RLock()
            return self._session_locks[session_id]

    @contextmanager
    def session_lock(self, session_id: str) -> Any:
        """公共 per-session 锁上下文管理器(替代外部调用 _get_session_lock)"""
        _validate_session_id(session_id)
        lock = self._get_session_lock(session_id)
        with lock:
            yield

    def create(
        self, name: str = "", goal: GoalContract | None = None, owner_id: str = ""
    ) -> Session:
        """创建新会话"""
        s = Session(
            name=name or f"session-{datetime.now(UTC):%Y%m%d-%H%M%S}",
            goal=goal,
            owner_id=owner_id,
        )
        with self._lock:
            self._cache[s.id] = s
            self._cache.move_to_end(s.id)
        self.save(s)
        return s

    def get(self, session_id: str, owner_id: str | None = None) -> Session:
        """获取会话(优先缓存,P-LOW-2-4: LRU + 加锁)

        PR-O: owner_id 非空时校验会话所有权(防 IDOR),兼容旧数据(owner_id 为空时跳过校验)
        PR-M: per-session 锁包裹缓存/文件加载 + owner_id 校验,返回引用但调用方需在锁内修改
        """
        _validate_session_id(session_id)
        lock = self._get_session_lock(session_id)
        with lock:
            with self._lock:
                if session_id in self._cache:
                    self._cache.move_to_end(session_id)
                    session = self._cache[session_id]
                else:
                    path = self.root / f"{session_id}.json"
                    if not path.exists():
                        raise KeyError(f"Session {session_id} not found")
                    session = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
                    self._cache[session_id] = session
                    self._cache.move_to_end(session_id)
                    while len(self._cache) > self._CACHE_MAX:
                        self._cache.popitem(last=False)
            # PR-O: IDOR 校验 — owner_id 非空且 session.owner_id 非空时才校验
            if owner_id is not None and session.owner_id and session.owner_id != owner_id:
                raise PermissionError(f"session {session_id} not owned by {owner_id}")
            return session

    def _lock_path(self, session_id: str) -> Path:
        """获取 per-session 锁文件路径"""
        return self.root / f".{session_id}.lock"

    def save(self, session: Session) -> None:
        """保存会话(per-session 文件锁 + 原子写入)

        PR-M: 用 per-session 内存锁包裹文件写入 + 缓存更新,与 get 共享锁,
        保证并发场景下保存与读取互斥(同 session 串行化)
        """
        # R1-01: save 入口校验 session.id 格式,防止路径遍历
        _validate_session_id(session.id)
        lock = self._get_session_lock(session.id)
        with lock:
            session.updated_at = datetime.now(UTC)
            path = self.root / f"{session.id}.json"
            data = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
            tmp_path = self.root / f".{session.id}.tmp"
            lock_path = self._lock_path(session.id)

            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    tmp_path.write_text(data, encoding="utf-8")
                    # PR-G/G-9: 会话文件仅 owner 可读写
                    os.chmod(str(tmp_path), stat.S_IRUSR | stat.S_IWUSR)
                    os.replace(str(tmp_path), str(path))
                finally:
                    try:
                        tmp_path.unlink()
                    except FileNotFoundError:
                        pass
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            # P-LOW-2-4: 缓存更新加锁 + LRU 驱逐
            with self._lock:
                self._cache[session.id] = session
                self._cache.move_to_end(session.id)
                while len(self._cache) > self._CACHE_MAX:
                    self._cache.popitem(last=False)

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有会话(摘要)"""
        sessions = []
        corrupted = 0
        for p in sorted(self.root.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "id": data["id"],
                        "name": data.get("name", ""),
                        "phase": data.get("phase", "init"),
                        "best_score": data.get("best_score", 0.0),
                        # PR-ROUND-3/D-04: 返回 current_score(前端显示当前分数)
                        "current_score": data.get("current_score", 0.0),
                        "updated_at": data.get("updated_at", ""),
                    }
                )
            except Exception as e:  # noqa: BLE001
                # P-LOW-2-9: 不再静默吞错,记录 warning 日志
                corrupted += 1
                _logger.warning(
                    "session_file_corrupt",
                    extra={"path": str(p), "err": str(e)},
                )
                continue
        if corrupted:
            _logger.warning("list_sessions_corrupt_count", extra={"corrupted": corrupted})
        return sessions

    def cleanup_session(self, session_id: str) -> None:
        """清理会话缓存(P-LOW-2-4,供 delete/WS 断开后调用)"""
        # 不校验 session_id — cleanup 可能传入已删除的非法 ID,需容错
        with self._lock:
            self._cache.pop(session_id, None)

    def delete(self, session_id: str, owner_id: str | None = None) -> bool:
        """删除会话

        PR-O: owner_id 非空时校验会话所有权(防 IDOR),兼容旧数据(owner_id 为空时跳过校验)
        PR-M: per-session 锁包裹整个删除流程,删除流程结束后清理 per-session 锁本身
        """
        _validate_session_id(session_id)
        lock = self._get_session_lock(session_id)
        with lock:
            # PR-O: IDOR 校验 — 先获取会话验证所有权
            # R1-02: 同时捕获 PermissionError(无权限删除 → 返回 False)
            if owner_id is not None:
                try:
                    self.get(session_id, owner_id=owner_id)
                except KeyError:
                    return False
                except PermissionError:
                    return False
            path = self.root / f"{session_id}.json"
            lock_path = self._lock_path(session_id)
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            deleted = False
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                with self._lock:
                    self._cache.pop(session_id, None)
                if path.exists():
                    path.unlink()
                    deleted = True
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            # PR-M: 删除流程结束后清理 per-session 内存锁(避免 _session_locks 无限增长)
            with self._locks_guard:
                self._session_locks.pop(session_id, None)
            # R1-02: 清理磁盘上的 .{session_id}.lock 文件
            lock_path.unlink(missing_ok=True)
            return deleted


_ = Any
