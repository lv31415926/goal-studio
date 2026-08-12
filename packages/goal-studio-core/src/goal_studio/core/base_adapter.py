"""BaseAdapter: 产物适配器抽象基类

适配器是 goal-studio 的核心扩展点。每种产物类型(SKILL/CODE/DOC/WORKFLOW)
实现一个 Adapter,提供 6 个方法的具体逻辑。

6 方法覆盖产物全生命周期:
  draft    - 目标 → 初始 DAG
  refine   - 应用用户精化补丁
  validate - 产物特定验证(如技能的9维评估,代码的lint)
  build    - DAG → 最终产物
  export   - 导出(file/git/feishu/...)
  install  - 安装到运行环境(可选,默认不支持)
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from goal_studio.core.contract import Artifact, GoalContract, Patch, Report
from goal_studio.core.node_graph import NodeGraph


class BaseAdapter(ABC):
    """产物适配器抽象基类

    子类必须实现 5 个抽象方法,install 可选覆盖。
    """

    # 适配器支持的产物类型,子类覆盖
    product_type: str = "custom"

    # 适配器名称,用于注册和显示
    name: str = "base"

    # 适配器描述
    description: str = "Base adapter"

    @abstractmethod
    def draft(self, goal: GoalContract) -> NodeGraph:
        """从目标契约生成初始三层 DAG

        Args:
            goal: 用户目标契约(四要素 + product_type)

        Returns:
            初始 NodeGraph,通常 L1 有 3-7 个任务节点
        """
        ...

    @abstractmethod
    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        """应用用户精化补丁到图

        Args:
            graph: 当前 NodeGraph
            patch: 用户精化补丁(来自 IntentParser)

        Returns:
            新的 NodeGraph (不可变语义,原 graph 不变)
        """
        ...

    @abstractmethod
    def validate(self, graph: NodeGraph) -> Report:
        """产物特定验证

        Args:
            graph: 待验证的 NodeGraph

        Returns:
            Report,包含 passed/score/dimensions/issues/suggestions
            score 用于棘轮门控比较
        """
        ...

    @abstractmethod
    def build(self, graph: NodeGraph) -> Artifact:
        """从 DAG 构建最终产物

        Args:
            graph: 已通过验证的 NodeGraph

        Returns:
            Artifact,包含产物内容、文件、元信息、graph 快照
        """
        ...

    @abstractmethod
    def export(self, artifact: Artifact, target: str) -> str:
        """导出产物到目标位置

        Args:
            artifact: 构建好的产物
            target: 导出目标,如 "file://./output" / "git://repo" / "feishu://doc"

        Returns:
            导出位置或 URL
        """
        ...

    def install(self, artifact: Artifact, env: dict[str, str]) -> str:
        """安装产物到运行环境(可选)

        默认不支持,子类按需覆盖。例如 SkillAdapter 可安装到 maxfun skill 目录。

        Args:
            artifact: 构建好的产物
            env: 环境信息,如 {"skill_dir": "/path/to/skills"}

        Returns:
            安装位置或状态信息
        """
        return f"Installation not supported for {self.product_type} adapter"

    # === 非抽象工具方法 ===

    def get_schema(self) -> dict[str, Any]:
        """返回适配器的节点槽位 schema,供前端渲染编辑表单

        子类可覆盖以提供具体 schema。默认返回空 schema。
        """
        return {}

    def estimate_complexity(self, graph: NodeGraph) -> int:
        """估算图的复杂度(1-10),用于决定是否需要分块执行

        默认按节点数估算,子类可覆盖。
        """
        n = len(graph.nodes)
        if n <= 5:
            return 2
        if n <= 10:
            return 4
        if n <= 20:
            return 6
        if n <= 50:
            return 8
        return 10


# 保留 Any 导入供 type hint
from typing import Any  # noqa: E402
