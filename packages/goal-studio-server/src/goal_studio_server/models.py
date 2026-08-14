"""Pydantic 模型 — 与前端 types/index.ts 镜像"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ProductType(StrEnum):
    SKILL = "skill"
    CODE = "code"
    DOC = "doc"
    WORKFLOW = "workflow"
    CUSTOM = "custom"


class PatchOp(StrEnum):
    ADD_NODE = "add_node"
    UPDATE_NODE = "update_node"
    REMOVE_NODE = "remove_node"
    ADD_EDGE = "add_edge"
    REMOVE_EDGE = "remove_edge"
    REORDER = "reorder"


class NodeModel(BaseModel):
    id: str
    layer: int = 1
    title: str
    description: str = ""
    node_type: str = "task"
    status: str = "draft"
    slots: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)
    position: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    metadata: dict[str, Any] = Field(default_factory=dict)


class EdgeModel(BaseModel):
    id: str
    source: str
    target: str
    label: str = ""
    edge_type: str = "data"
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeGraphModel(BaseModel):
    nodes: dict[str, NodeModel] = Field(default_factory=dict)
    edges: list[EdgeModel] = Field(default_factory=list)
    root_id: str | None = None
    version: int = 1


class GoalContractModel(BaseModel):
    goal: str = Field(max_length=10000)  # PR-ROUND-1/A-02: 限制 goal 描述长度
    prerequisites: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    product_type: ProductType = ProductType.SKILL
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatchModel(BaseModel):
    op: PatchOp
    target_node_id: str | None = None
    target_node_id_2: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    new_node: dict[str, Any] | None = None
    position: int | None = None
    source: str = "text"
    rationale: str = Field(default="", max_length=2000)  # PR-ROUND-1/A-02


class ReportModel(BaseModel):
    passed: bool
    score: float = 0.0
    dimensions: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    evaluator: str = "auto"


class ArtifactModel(BaseModel):
    product_type: ProductType
    content: str
    files: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    graph_snapshot: dict[str, Any] = Field(default_factory=dict)
    version: str = "0.1.0"


class SessionModel(BaseModel):
    id: str
    name: str = ""
    goal: GoalContractModel | None = None
    graph: NodeGraphModel | None = None
    phase: str = "init"
    best_score: float = 0.0
    current_score: float = 0.0
    ratchet_history: list[dict[str, Any]] = Field(default_factory=list)
    dialog: list[dict[str, str]] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    # PR-ROUND-3/D-03: 最佳产物 ID(供前端触发 install)
    best_artifact_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    id: str
    name: str
    phase: str
    best_score: float
    # PR-ROUND-3/D-04: 添加 current_score(前端显示当前分数)
    current_score: float = 0.0
    updated_at: str


class CreateSessionRequest(BaseModel):
    name: str = Field(default="", max_length=200)  # PR-ROUND-1/A-02: 限制会话名长度
    goal: GoalContractModel | None = None


class DraftRequest(BaseModel):
    goal: GoalContractModel


class RefineRequest(BaseModel):
    patch: PatchModel


class AdapterInfo(BaseModel):
    product_type: str
    name: str
    description: str
