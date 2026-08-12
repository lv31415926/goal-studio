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
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from goal_studio.core.contract import GoalContract
from goal_studio.core.node_graph import NodeGraph


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
    """历史最高分"""

    best_graph: NodeGraph | None = None
    """历史最佳图"""

    ratchet_history: list[dict[str, Any]] = field(default_factory=list)
    """棘轮门控历史"""

    dialog: list[dict[str, str]] = field(default_factory=list)
    """对话历史(供 IntentParser 上下文)"""

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    metadata: dict[str, Any] = field(default_factory=dict)

    def add_dialog(self, role: str, content: str) -> None:
        """添加对话"""
        self.dialog.append({"role": role, "content": content})
        self.updated_at = datetime.now()

    def update_graph(self, graph: NodeGraph) -> None:
        """更新当前图"""
        self.graph = graph
        self.updated_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "goal": _goal_to_dict(self.goal) if self.goal else None,
            "graph": self.graph.to_dict() if self.graph else None,
            "phase": self.phase.value,
            "best_score": self.best_score,
            "best_graph": self.best_graph.to_dict() if self.best_graph else None,
            "ratchet_history": self.ratchet_history,
            "dialog": self.dialog,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        s = cls(
            id=data["id"],
            name=data.get("name", ""),
            phase=SessionPhase(data.get("phase", "init")),
            best_score=data.get("best_score", 0.0),
            ratchet_history=data.get("ratchet_history", []),
            dialog=data.get("dialog", []),
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


class SessionManager:
    """会话管理器"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}

    def create(self, name: str = "", goal: GoalContract | None = None) -> Session:
        """创建新会话"""
        s = Session(name=name or f"session-{datetime.now():%Y%m%d-%H%M%S}", goal=goal)
        self._cache[s.id] = s
        self.save(s)
        return s

    def get(self, session_id: str) -> Session:
        """获取会话(优先缓存)"""
        if session_id in self._cache:
            return self._cache[session_id]
        path = self.root / f"{session_id}.json"
        if not path.exists():
            raise KeyError(f"Session {session_id} not found")
        s = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
        self._cache[session_id] = s
        return s

    def save(self, session: Session) -> None:
        """保存会话"""
        session.updated_at = datetime.now()
        path = self.root / f"{session.id}.json"
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._cache[session.id] = session

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有会话(摘要)"""
        sessions = []
        for p in sorted(self.root.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "id": data["id"],
                        "name": data.get("name", ""),
                        "phase": data.get("phase", "init"),
                        "best_score": data.get("best_score", 0.0),
                        "updated_at": data.get("updated_at", ""),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        return sessions

    def delete(self, session_id: str) -> bool:
        """删除会话"""
        path = self.root / f"{session_id}.json"
        self._cache.pop(session_id, None)
        if path.exists():
            path.unlink()
            return True
        return False


_ = Any
