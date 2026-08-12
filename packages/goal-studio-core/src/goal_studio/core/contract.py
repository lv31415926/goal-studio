"""核心数据契约: GoalContract / Patch / Report / Artifact

这些是适配器接口的输入输出类型,所有产物类型共用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProductType(str, Enum):
    """支持的产物类型"""

    SKILL = "skill"
    CODE = "code"
    DOC = "doc"
    WORKFLOW = "workflow"
    CUSTOM = "custom"


class PatchOp(str, Enum):
    """节点补丁操作类型"""

    ADD_NODE = "add_node"
    UPDATE_NODE = "update_node"
    REMOVE_NODE = "remove_node"
    ADD_EDGE = "add_edge"
    REMOVE_EDGE = "remove_edge"
    REORDER = "reorder"


@dataclass
class GoalContract:
    """用户目标契约 — 创作的起点

    四要素: goal + prerequisites + constraints + success_criteria
    加上 product_type 决定使用哪个适配器。
    """

    goal: str
    """用户要达成的目标,自然语言描述"""

    prerequisites: list[str] = field(default_factory=list)
    """先决条件: 已有的资源、上下文、输入"""

    constraints: list[str] = field(default_factory=list)
    """约束: 必须满足的限制,如"必须用中文输出""不能依赖网络" """

    success_criteria: list[str] = field(default_factory=list)
    """成功标准: 可验证的验收条件"""

    product_type: ProductType = ProductType.SKILL
    """产物类型,决定使用哪个适配器"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """额外元信息: 作者、版本、标签等"""

    def validate(self) -> list[str]:
        """校验契约完整性,返回问题列表(空=通过)"""
        issues: list[str] = []
        if not self.goal.strip():
            issues.append("goal 不能为空")
        if not self.success_criteria:
            issues.append("建议提供至少 1 条 success_criteria")
        return issues


@dataclass
class Patch:
    """用户精化补丁 — Refine 阶段的输入

    IntentParser 把多模态输入解析为 Patch,再由适配器应用到 NodeGraph。
    """

    op: PatchOp
    """操作类型"""

    target_node_id: str | None = None
    """目标节点 ID (add/remove edge 时为源节点)"""

    target_node_id_2: str | None = None
    """第二个目标节点 ID (仅 add/remove edge 使用)"""

    slots: dict[str, Any] = field(default_factory=dict)
    """节点槽位更新: {prop: value}"""

    new_node: dict[str, Any] | None = None
    """新增节点的初始数据"""

    position: int | None = None
    """reorder 操作的目标位置"""

    source: str = "voice"
    """补丁来源: voice/text/mouse"""

    rationale: str = ""
    """补丁理由(供审计)"""


@dataclass
class Report:
    """验证报告 — Validate 阶段的输出"""

    passed: bool
    """是否通过验证"""

    score: float = 0.0
    """0-100 分,棘轮门控的依据"""

    dimensions: dict[str, float] = field(default_factory=dict)
    """各维度分数: {"structure": 85, "effect": 70, ...}"""

    issues: list[str] = field(default_factory=list)
    """发现的问题"""

    suggestions: list[str] = field(default_factory=list)
    """改进建议"""

    evaluator: str = "auto"
    """评估者: auto/llm/human/peer-agent"""


@dataclass
class Artifact:
    """最终产物 — Build 阶段的输出"""

    product_type: ProductType
    """产物类型"""

    content: str
    """产物主体内容(SKILL.md / 代码 / 文档文本 / workflow json)"""

    files: dict[str, str] = field(default_factory=dict)
    """多文件产物: {path: content}"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """产物元信息"""

    graph_snapshot: dict[str, Any] = field(default_factory=dict)
    """生成此产物的 NodeGraph 快照,用于溯源"""

    version: str = "0.1.0"
    """产物版本"""

    def save_to(self, path: str) -> str:
        """保存到目录(占位,由 ArtifactStore 实现)"""
        raise NotImplementedError("Use ArtifactStore.save()")
