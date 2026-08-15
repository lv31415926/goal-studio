"""Plugin SDK: 自定义适配器开发工具包

提供:
  1. AdapterMeta — 适配器元数据声明(名称/版本/作者/依赖)
  2. load_from_directory — 从本地目录热加载适配器(.py 文件)
  3. scaffold — 生成适配器脚手架代码
  4. validate_adapter — 验证适配器是否符合规范

使用方式:
  # 脚手架生成新适配器
  python -m goal_studio.core.plugin_sdk scaffold --name MyAdapter --type custom

  # 从目录热加载
  registry.load_from_directory("./my_adapters")
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goal_studio.core.base_adapter import BaseAdapter

_logger = logging.getLogger(__name__)

# PR-O: 插件目录白名单 — 只允许加载项目内 ./adapters 或 ~/.goal-studio/adapters
_ALLOWED_PLUGIN_DIRS: list[Path] = [
    Path.cwd() / "adapters",
    Path.home() / ".goal-studio" / "adapters",
]
# 支持环境变量 GOAL_STUDIO_PLUGIN_DIRS 扩展(冒号分隔多目录)
_extra = os.getenv("GOAL_STUDIO_PLUGIN_DIRS", "")
if _extra:
    for _d in _extra.split(os.pathsep):
        _d = _d.strip()
        if _d:
            _ALLOWED_PLUGIN_DIRS.append(Path(_d))


@dataclass
class AdapterMeta:
    """适配器元数据 — 供 registry 和文档站展示"""

    name: str
    """适配器显示名"""

    product_type: str
    """产物类型标识(唯一 key)"""

    version: str = "0.1.0"
    """语义化版本"""

    author: str = ""
    """作者"""

    description: str = ""
    """一句话描述"""

    dependencies: list[str] = field(default_factory=list)
    """依赖的其他 product_type(如 workflow 依赖 skill)"""

    homepage: str = ""
    """项目主页 URL"""

    @classmethod
    def from_adapter(cls, adapter: BaseAdapter) -> AdapterMeta:
        """从适配器实例提取元数据"""
        meta_attr = getattr(adapter, "_meta", None)
        if isinstance(meta_attr, AdapterMeta):
            return meta_attr
        return cls(
            name=adapter.name,
            product_type=adapter.product_type,
            description=adapter.description,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "product_type": self.product_type,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "dependencies": self.dependencies,
            "homepage": self.homepage,
        }


def load_from_directory(
    directory: str | Path,
    registry: Any | None = None,
) -> list[str]:
    """从本地目录热加载适配器

    扫描目录下所有 *.py 文件,查找 BaseAdapter 子类并注册。

    Args:
        directory: 适配器目录路径
        registry: 注册中心(默认使用全局 registry)

    Returns:
        成功加载的适配器 product_type 列表
    """
    if registry is None:
        from goal_studio.core.adapter_registry import get_default_registry
        registry = get_default_registry()

    dir_path = Path(directory).resolve()
    if not dir_path.is_dir():
        return []

    # PR-O: 路径白名单校验 — 只允许加载受信任目录下的适配器
    allowed = any(
        str(dir_path) == str(p.resolve()) or str(dir_path).startswith(str(p.resolve()) + os.sep)
        for p in _ALLOWED_PLUGIN_DIRS
    )
    if not allowed:
        _logger.warning("plugin_dir_blocked", extra={"dir": str(dir_path)})
        return []

    loaded: list[str] = []
    for py_file in sorted(dir_path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        # PR-O: 签名校验 — 检查是否有对应的 .signature 文件
        sig_file = py_file.with_suffix(".signature")
        if not sig_file.exists():
            _logger.warning("plugin_signature_missing", extra={"file": str(py_file)})
            continue
        try:
            module_name = f"_gs_plugin_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # 查找模块中的 BaseAdapter 子类
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseAdapter)
                    and attr is not BaseAdapter
                    and not attr.__name__.startswith("_")
                ):
                    try:
                        instance = attr()
                        registry.register(instance)
                        loaded.append(instance.product_type)
                    except ValueError:
                        pass  # 已注册,跳过
        except Exception as e:  # noqa: BLE001
            print(f"Warning: failed to load adapter from {py_file}: {e}", file=sys.stderr)

    return loaded


def validate_adapter(adapter: BaseAdapter) -> list[str]:
    """验证适配器是否符合规范

    检查:
      - product_type 非空且为合法标识符
      - name 非空
      - 5 个抽象方法都已实现
      - draft() 返回 NodeGraph
      - build() 返回 Artifact

    Returns:
        问题列表(空列表表示通过)
    """
    issues: list[str] = []

    # 基本属性
    if not adapter.product_type or adapter.product_type == "custom":
        issues.append("product_type 未设置(不能为默认值 'custom')")
    if not adapter.name or adapter.name == "base":
        issues.append("name 未设置(不能为默认值 'base')")

    # 检查抽象方法是否被覆盖(非 abstract)
    abstract_methods = ["draft", "refine", "validate", "build", "export"]
    for method_name in abstract_methods:
        method = getattr(type(adapter), method_name, None)
        if method is None:
            issues.append(f"缺少方法: {method_name}")
        elif getattr(method, "__isabstractmethod__", False):
            issues.append(f"方法未实现: {method_name}")

    # 检查方法签名
    sig_checks = {
        "draft": ["goal"],
        "refine": ["graph", "patch"],
        "validate": ["graph"],
        "build": ["graph"],
        "export": ["artifact", "target"],
    }
    for method_name, expected_params in sig_checks.items():
        method = getattr(type(adapter), method_name, None)
        if method is None:
            continue
        try:
            sig = inspect.signature(method)
            params = [p for p in sig.parameters if p != "self"]
            for ep in expected_params:
                if ep not in params:
                    issues.append(f"{method_name}() 缺少参数: {ep}")
        except (ValueError, TypeError):
            pass

    return issues


# === 脚手架模板 ===

SCAFFOLD_TEMPLATE = '''"""{adapter_name}: {description}

自定义适配器示例 — 由 goal-studio Plugin SDK 脚手架生成。

产物类型: {product_type}
"""
from __future__ import annotations

from typing import Any

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, Report
from goal_studio.core.node_graph import (
    Edge, Node, NodeGraph, NodeLayer, NodeStatus,
)
from goal_studio.core.plugin_sdk import AdapterMeta
from goal_studio.llm import LLMMessage, get_llm


class {class_name}(BaseAdapter):
    """{description}"""

    product_type = "{product_type}"
    name = "{adapter_name}"
    description = "{description}"

    _meta = AdapterMeta(
        name="{adapter_name}",
        product_type="{product_type}",
        version="0.1.0",
        author="your-name",
        description="{description}",
    )

    def draft(self, goal: GoalContract) -> NodeGraph:
        """从目标契约生成初始三层 DAG"""
        # TODO: 实现你的 DAG 生成逻辑
        graph = NodeGraph()
        # 示例: 创建一个简单的 L1 节点
        node = Node(
            id="l1-start",
            layer=NodeLayer.L1_PLANNING,
            title="开始",
            description=f"处理: {{goal.goal[:50]}}",
            node_type="task",
        )
        graph.add_node(node)
        graph.root_id = "l1-start"
        return graph

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        """应用精化补丁"""
        new_graph = NodeGraph.from_dict(graph.to_dict())
        if patch.target_node_id in new_graph.nodes:
            node = new_graph.nodes[patch.target_node_id]
            for k, v in patch.slots.items():
                node.slots[k] = v
            new_graph.version += 1
        return new_graph

    def validate(self, graph: NodeGraph) -> Report:
        """产物特定验证"""
        issues = []
        if not graph.nodes:
            issues.append("图为空")
        return Report(
            passed=len(issues) == 0,
            score=80.0 if not issues else 0.0,
            evaluator="{adapter_name}",
            dimensions={{"completeness": 80.0}},
            issues=issues,
            suggestions=[],
        )

    def build(self, graph: NodeGraph) -> Artifact:
        """从 DAG 构建产物"""
        content = f"# {{self.product_type}} 产物\\n\\n由 {adapter_name} 生成。\\n"
        return Artifact(
            product_type=(
                graph.nodes[graph.root_id].node_type if graph.root_id else self.product_type
            ),
            content=content,
            files={{"output.md": content}},
            graph_snapshot=graph.to_dict(),
            metadata={{"adapter": self.name}},
        )

    def export(self, artifact: Artifact, target: str) -> str:
        """导出产物"""
        if target.startswith("file://"):
            output_path = target.replace("file://", "")
            from pathlib import Path
            p = Path(output_path)
            p.mkdir(parents=True, exist_ok=True)
            for fname, content in artifact.files.items():
                (p / fname).write_text(content, encoding="utf-8")
            return str(p)
        return f"Export to {{target}} not supported"
'''


def scaffold(
    name: str,
    product_type: str,
    output_dir: str | Path = ".",
    description: str = "",
) -> Path:
    """生成适配器脚手架代码

    Args:
        name: 适配器显示名(如 "MyAdapter")
        product_type: 产物类型标识(如 "custom")
        output_dir: 输出目录
        description: 一句话描述

    Returns:
        生成的文件路径
    """
    # 类名: PascalCase(先拆词再逐词 capitalize,避免 "customAdapter" 被压成 "Customadapter")
    words = name.replace("-", "_").replace(" ", "_").lower().split("_")
    words = [w for w in words if w]
    if not words or words[-1] != "adapter":
        words.append("adapter")
    class_name = "".join(w.capitalize() for w in words)

    # 文件名: snake_case
    file_name = name.replace("-", "_").replace(" ", "_").lower()
    if not file_name.endswith("_adapter"):
        file_name += "_adapter"

    if not description:
        description = f"{name} 适配器"

    code = SCAFFOLD_TEMPLATE.format(
        adapter_name=name,
        class_name=class_name,
        product_type=product_type,
        description=description,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / f"{file_name}.py"
    file_path.write_text(code, encoding="utf-8")
    # PR-O 配套: load_plugins 要求 .py 旁存在 .signature 才会加载(信任标记)。
    # scaffold 同步生成,打通「scaffold → 编辑 → 放入白名单目录 → load-plugins」链路
    sig_path = output_path / f"{file_name}.signature"
    sig_path.write_text(
        json.dumps(
            {
                "generated_by": "goal-studio scaffold",
                "adapter": name,
                "product_type": product_type,
                "created_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return file_path
