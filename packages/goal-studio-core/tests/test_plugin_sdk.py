"""Plugin SDK 测试: AdapterMeta / load_from_directory / validate_adapter / scaffold"""
from __future__ import annotations

from pathlib import Path

from goal_studio.core import plugin_sdk
from goal_studio.core.adapter_registry import AdapterRegistry
from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, Report
from goal_studio.core.node_graph import NodeGraph
from goal_studio.core.plugin_sdk import (
    AdapterMeta,
    load_from_directory,
    scaffold,
    validate_adapter,
)

# === 测试用适配器 ===

class _GoodAdapter(BaseAdapter):
    """符合规范的适配器"""

    product_type = "goodtest"
    name = "GoodTest"
    description = "A good test adapter"

    _meta = AdapterMeta(
        name="GoodTest",
        product_type="goodtest",
        version="1.2.3",
        author="tester",
        description="A good test adapter",
    )

    def draft(self, goal: GoalContract) -> NodeGraph:
        return NodeGraph()

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        return graph

    def validate(self, graph: NodeGraph) -> Report:
        return Report(passed=True, score=90.0, evaluator="goodtest")

    def build(self, graph: NodeGraph) -> Artifact:
        return Artifact(product_type="goodtest", content="x")

    def export(self, artifact: Artifact, target: str) -> str:
        return target


class _BadAdapter(BaseAdapter):
    """保留默认 product_type/name 的适配器(应被 validate 标记)"""

    def draft(self, goal: GoalContract) -> NodeGraph:
        return NodeGraph()

    def refine(self, graph: NodeGraph, patch: Patch) -> NodeGraph:
        return graph

    def validate(self, graph: NodeGraph) -> Report:
        return Report(passed=True, score=0.0, evaluator="bad")

    def build(self, graph: NodeGraph) -> Artifact:
        return Artifact(product_type="custom", content="")

    def export(self, artifact: Artifact, target: str) -> str:
        return target


# 供热加载测试写入磁盘的适配器源码
_PLUGIN_SOURCE = '''
from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import Artifact, GoalContract, Patch, Report
from goal_studio.core.node_graph import NodeGraph


class HotLoadedAdapter(BaseAdapter):
    product_type = "hotloaded"
    name = "HotLoaded"
    description = "hot loaded"

    def draft(self, goal):
        return NodeGraph()

    def refine(self, graph, patch):
        return graph

    def validate(self, graph):
        return Report(passed=True, score=1.0, evaluator="hot")

    def build(self, graph):
        return Artifact(product_type="hotloaded", content="h")

    def export(self, artifact, target):
        return target
'''


# === AdapterMeta ===

class TestAdapterMeta:
    def test_from_adapter_with_meta(self):
        meta = AdapterMeta.from_adapter(_GoodAdapter())
        assert meta.product_type == "goodtest"
        assert meta.version == "1.2.3"
        assert meta.author == "tester"

    def test_from_adapter_without_meta(self):
        meta = AdapterMeta.from_adapter(_BadAdapter())
        # 无 _meta 属性 → 从实例字段回退构造
        assert meta.product_type == "custom"
        assert meta.name == "base"

    def test_to_dict(self):
        meta = AdapterMeta(name="X", product_type="x", dependencies=["y"])
        d = meta.to_dict()
        assert d["name"] == "X"
        assert d["product_type"] == "x"
        assert d["dependencies"] == ["y"]
        assert "version" in d


# === validate_adapter ===

class TestValidateAdapter:
    def test_valid_adapter_passes(self):
        issues = validate_adapter(_GoodAdapter())
        assert issues == []

    def test_default_product_type_flagged(self):
        issues = validate_adapter(_BadAdapter())
        assert any("product_type" in i for i in issues)
        assert any("name" in i for i in issues)

    def test_missing_method_flagged(self):
        # 鸭子类型对象: 缺 export 方法 → validate 应标记
        # (不继承 BaseAdapter,因为抽象类无法实例化;validate_adapter 运行时按属性检查)
        class _Partial:
            product_type = "partial"
            name = "Partial"

            def draft(self, goal):
                return None

            def refine(self, graph, patch):
                return None

            def validate(self, graph):
                return None

            def build(self, graph):
                return None

        issues = validate_adapter(_Partial())  # type: ignore[arg-type]
        assert any("export" in i for i in issues)


# === scaffold ===

class TestScaffold:
    def test_scaffold_generates_file(self, tmp_path: Path):
        out = scaffold(
            name="my-custom",
            product_type="customtype",
            output_dir=tmp_path,
            description="自定义适配器",
        )
        assert out.exists()
        assert out.name == "my_custom_adapter.py"
        content = out.read_text(encoding="utf-8")
        assert "class MyCustomAdapter" in content
        assert 'product_type = "customtype"' in content

    def test_scaffold_appends_adapter_suffix(self, tmp_path: Path):
        out = scaffold(name="plain", product_type="plain", output_dir=tmp_path)
        assert out.name == "plain_adapter.py"
        assert "PlainAdapter" in out.read_text(encoding="utf-8")

    def test_scaffold_default_description(self, tmp_path: Path):
        out = scaffold(name="nodesc", product_type="nodesc", output_dir=tmp_path)
        assert "nodesc 适配器" in out.read_text(encoding="utf-8")


# === load_from_directory ===

class TestLoadFromDirectory:
    def test_nonexistent_dir_returns_empty(self):
        assert load_from_directory("/nonexistent/path/xyz", AdapterRegistry()) == []

    def test_blocked_dir_returns_empty(self, tmp_path: Path, monkeypatch):
        # 白名单不含 tmp_path → 被拦截
        monkeypatch.setattr(plugin_sdk, "_ALLOWED_PLUGIN_DIRS", [])
        reg = AdapterRegistry()
        assert load_from_directory(tmp_path, reg) == []

    def test_load_valid_adapter(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(plugin_sdk, "_ALLOWED_PLUGIN_DIRS", [tmp_path])
        (tmp_path / "hot_adapter.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
        (tmp_path / "hot_adapter.signature").write_text("sig", encoding="utf-8")
        reg = AdapterRegistry()
        loaded = load_from_directory(tmp_path, reg)
        assert "hotloaded" in loaded
        assert reg.get("hotloaded").name == "HotLoaded"

    def test_missing_signature_skipped(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(plugin_sdk, "_ALLOWED_PLUGIN_DIRS", [tmp_path])
        (tmp_path / "nosig.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
        # 不创建 .signature → 跳过
        reg = AdapterRegistry()
        assert load_from_directory(tmp_path, reg) == []

    def test_underscore_file_skipped(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(plugin_sdk, "_ALLOWED_PLUGIN_DIRS", [tmp_path])
        (tmp_path / "_private.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
        (tmp_path / "_private.signature").write_text("sig", encoding="utf-8")
        reg = AdapterRegistry()
        assert load_from_directory(tmp_path, reg) == []

    def test_duplicate_register_skipped(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(plugin_sdk, "_ALLOWED_PLUGIN_DIRS", [tmp_path])
        (tmp_path / "a_hot.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
        (tmp_path / "a_hot.signature").write_text("sig", encoding="utf-8")
        (tmp_path / "b_hot.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
        (tmp_path / "b_hot.signature").write_text("sig", encoding="utf-8")
        reg = AdapterRegistry()
        loaded = load_from_directory(tmp_path, reg)
        # 第二个文件注册同 product_type 抛 ValueError 被吞掉,只加载一次
        assert loaded.count("hotloaded") == 1

    def test_broken_module_warns_not_raises(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.setattr(plugin_sdk, "_ALLOWED_PLUGIN_DIRS", [tmp_path])
        (tmp_path / "broken.py").write_text("raise RuntimeError('boom')", encoding="utf-8")
        (tmp_path / "broken.signature").write_text("sig", encoding="utf-8")
        reg = AdapterRegistry()
        loaded = load_from_directory(tmp_path, reg)
        assert loaded == []
        assert "failed to load" in capsys.readouterr().err
