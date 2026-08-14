"""AdapterRegistry: 适配器注册中心 + 热加载

按 product_type 注册适配器实例,运行时按 goal.product_type 查找。
支持 entry_points 热加载第三方适配器。
"""
from __future__ import annotations

from typing import Any

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import GoalContract


class AdapterRegistry:
    """适配器注册中心"""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}
        self._aliases: dict[str, str] = {}

    def register(self, adapter: BaseAdapter, aliases: list[str] | None = None) -> None:
        """注册适配器"""
        key = adapter.product_type
        if key in self._adapters:
            raise ValueError(f"Adapter for {key} already registered")
        self._adapters[key] = adapter
        for alias in aliases or []:
            self._aliases[alias] = key

    def unregister(self, product_type: str) -> None:
        """注销适配器"""
        self._adapters.pop(product_type, None)
        # 清理别名
        self._aliases = {a: k for a, k in self._aliases.items() if k != product_type}

    def get(self, product_type: str) -> BaseAdapter:
        """按 product_type 或别名获取适配器"""
        key = self._aliases.get(product_type, product_type)
        if key not in self._adapters:
            raise KeyError(
                f"No adapter for product_type={product_type}. "
                f"Registered: {list(self._adapters.keys())}"
            )
        return self._adapters[key]

    def get_for_goal(self, goal: GoalContract) -> BaseAdapter:
        """按 GoalContract 获取适配器"""
        return self.get(goal.product_type.value)

    def list_adapters(self) -> list[dict[str, str]]:
        """列出所有已注册适配器"""
        return [
            {
                "product_type": a.product_type,
                "name": a.name,
                "description": a.description,
            }
            for a in self._adapters.values()
        ]

    def load_entry_points(self) -> None:
        """从 setuptools entry_points 热加载第三方适配器

        entry_point group: goal_studio.adapters
        """
        try:
            from importlib.metadata import entry_points

            eps = entry_points(group="goal_studio.adapters")
            for ep in eps:
                adapter_cls = ep.load()
                if isinstance(adapter_cls, type) and issubclass(adapter_cls, BaseAdapter):
                    self.register(adapter_cls())
        except Exception as e:  # noqa: BLE001
            # 热加载失败不致命,记录即可
            import sys

            print(f"Warning: failed to load adapter entry_points: {e}", file=sys.stderr)

    def load_from_directory(self, directory: str) -> list[str]:
        """从本地目录热加载适配器(Plugin SDK)

        扫描目录下所有 *.py 文件,查找 BaseAdapter 子类并注册。

        Args:
            directory: 适配器目录路径

        Returns:
            成功加载的适配器 product_type 列表
        """
        from goal_studio.core.plugin_sdk import load_from_directory as _load
        return _load(directory, self)


# 全局默认注册中心
_default_registry: AdapterRegistry | None = None


def get_default_registry() -> AdapterRegistry:
    """获取全局默认注册中心,首次调用时注册内置适配器"""
    global _default_registry
    if _default_registry is None:
        _default_registry = AdapterRegistry()
        _register_builtin_adapters(_default_registry)
    return _default_registry


def _register_builtin_adapters(registry: AdapterRegistry) -> None:
    """注册内置适配器(skill/code/doc/workflow)

    延迟导入避免循环依赖。MVP 阶段先注册占位实现。
    """
    try:
        from goal_studio.adapters.skill_adapter import SkillAdapter

        registry.register(SkillAdapter(), aliases=["技能", "skill"])
    except ImportError:
        pass
    try:
        from goal_studio.adapters.code_adapter import CodeAdapter

        registry.register(CodeAdapter(), aliases=["代码", "code"])
    except ImportError:
        pass
    try:
        from goal_studio.adapters.doc_adapter import DocAdapter

        registry.register(DocAdapter(), aliases=["文档", "doc"])
    except ImportError:
        pass
    try:
        from goal_studio.adapters.workflow_adapter import WorkflowAdapter

        registry.register(WorkflowAdapter(), aliases=["工作流", "workflow"])
    except ImportError:
        pass


# 保留 Any 导入
_ = Any
