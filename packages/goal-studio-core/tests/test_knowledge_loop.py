"""G1: KnowledgeStore 持久化 + prior_lessons prompt 注入"""
from __future__ import annotations

from pathlib import Path

from goal_studio.core.base_adapter import BaseAdapter
from goal_studio.core.contract import GoalContract, ProductType
from goal_studio.core.knowledge_store import (
    InMemoryKnowledgeStore,
    KnowledgeEntry,
    LocalFileKnowledgeStore,
)


def _entry(key: str, value: str, tags: list[str] | None = None) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=key, category="experience", key=key, value=value,
        source="auto", tags=tags or [],
    )


def test_local_store_roundtrip(tmp_path: Path) -> None:
    """写入 → 重开(新实例) → 数据仍在;查询/更新/删除生效"""
    path = tmp_path / "k.json"
    store = LocalFileKnowledgeStore(path)
    store.put(_entry("r1", "量化工作流缺回测节点会被回滚", tags=["量化", "回测"]))
    store.put(_entry("r2", "技能产物需含触发词", tags=["skill"]))

    store2 = LocalFileKnowledgeStore(path)
    assert store2.get("r1") is not None
    assert store2.get("r1").value.startswith("量化")
    cats = store2.list_categories()
    assert cats == ["experience"]
    # keyword 查询
    assert len(store2.query(keyword="回测")) == 1
    # category+tags 过滤
    assert len(store2.query(category="experience", tags=["skill"])) == 1
    # update / delete
    store2.update("r1", "新内容", confidence=0.9)
    assert store2.get("r1").confidence == 0.9
    assert store2.delete("r1") is True
    assert store2.get("r1") is None


def test_local_store_corrupt_file_degrades(tmp_path: Path) -> None:
    """损坏文件降级为空库,不抛异常"""
    path = tmp_path / "k.json"
    path.write_text("{not json", encoding="utf-8")
    store = LocalFileKnowledgeStore(path)
    assert store.query() == []


def test_prior_lessons_section_renders() -> None:
    """BaseAdapter._prior_lessons_section: 注入渲染 + 边界容错"""
    goal = GoalContract(
        goal="创建量化技能",
        constraints=[], success_criteria=[],
        product_type=ProductType.SKILL,
        metadata={"prior_lessons": ["缺回测节点会被回滚", "触发词必填"]},
    )
    section = BaseAdapter._prior_lessons_section(goal)
    assert "历史经验" in section
    assert "1. 缺回测节点会被回滚" in section
    # 空/异常 metadata 不渲染
    empty = GoalContract(goal="g", constraints=[], success_criteria=[],
                         product_type=ProductType.SKILL, metadata={})
    assert BaseAdapter._prior_lessons_section(empty) == ""
    bad = GoalContract(goal="g", constraints=[], success_criteria=[],
                       product_type=ProductType.SKILL, metadata={"prior_lessons": "not-a-list"})
    assert BaseAdapter._prior_lessons_section(bad) == ""


def test_in_memory_store_still_works() -> None:
    """既有内存实现回归(测试基础设施依赖)"""
    store = InMemoryKnowledgeStore()
    store.put(_entry("k1", "v1"))
    assert store.get("k1").value == "v1"
# ==== Voyager: _prior_patterns_section few-shot 渲染 ====

def test_prior_patterns_section_renders() -> None:
    """合法 pattern 列表渲染为参考结构 few-shot"""
    goal = GoalContract(
        goal="创建量化技能", constraints=[], success_criteria=[],
        product_type=ProductType.SKILL,
        metadata={"prior_patterns": [{
            "goal": "创建回测技能",
            "product_type": "skill",
            "l1_nodes": [
                {"title": "需求分析", "description": "解析目标四要素"},
                {"title": "回测引擎", "description": "设计回测流程"},
            ],
            "score": 88.0,
        }]},
    )
    section = BaseAdapter._prior_patterns_section(goal)
    assert "可复用的成功结构" in section
    assert "创建回测技能" in section
    assert "- 需求分析: 解析目标四要素" in section


def test_prior_patterns_section_multiple_and_limit() -> None:
    """多条 pattern 渲染,仅取前 2 例(few-shot 上限)"""
    pat = {"goal": "g", "l1_nodes": [{"title": "t", "description": "d"}]}
    goal = GoalContract(goal="g", constraints=[], success_criteria=[],
                        product_type=ProductType.SKILL,
                        metadata={"prior_patterns": [pat, pat, pat]})
    section = BaseAdapter._prior_patterns_section(goal)
    # 计数口径: case 条目行(尾部组合提示文案也含"参考结构"字样,不计入)
    assert section.count("参考结构(历史成功案例") == 2


def test_prior_patterns_section_edge_cases() -> None:
    """空/坏数据不渲染,不抛异常"""
    def mk(meta: dict) -> GoalContract:
        return GoalContract(goal="g", constraints=[], success_criteria=[],
                            product_type=ProductType.SKILL, metadata=meta)

    empty_pattern = [{"goal": "x", "l1_nodes": []}]
    cases = [{}, {"prior_patterns": "bad"},
             {"prior_patterns": ["not-dict"]}, {"prior_patterns": empty_pattern}]
    for meta in cases:
        assert BaseAdapter._prior_patterns_section(mk(meta)) == ""
    # 坏节点条目跳过,好条目保留
    section = BaseAdapter._prior_patterns_section(mk({"prior_patterns": [{
        "goal": "x",
        "l1_nodes": ["bad-node", {"title": "好节点", "description": "d"}],
    }]}))
    assert "好节点" in section and "bad-node" not in section
# ==== Voyager 深化: artifact_outline 渲染 + 组合复用提示 ====

def test_prior_patterns_renders_artifact_outline() -> None:
    """pattern 含产物骨架时, few-shot 渲染骨架预览段"""
    goal = GoalContract(
        goal="创建技能", constraints=[], success_criteria=[],
        product_type=ProductType.SKILL,
        metadata={"prior_patterns": [{
            "goal": "成功案例", "l1_nodes": [{"title": "需求", "description": "d"}],
            "artifact_outline": {
                "SKILL.md": ["# 技能", "## §1 命令入口", "## §2 工作流"],
            },
        }]},
    )
    section = BaseAdapter._prior_patterns_section(goal)
    assert "产物骨架(SKILL.md)" in section
    assert "# 技能" in section


def test_prior_patterns_composition_hint() -> None:
    """渲染尾部含组合复用提示(Voyager 组合性)"""
    goal = GoalContract(
        goal="创建技能", constraints=[], success_criteria=[],
        product_type=ProductType.SKILL,
        metadata={"prior_patterns": [{
            "goal": "g", "l1_nodes": [{"title": "t", "description": "d"}],
        }]},
    )
    section = BaseAdapter._prior_patterns_section(goal)
    assert "可整体复用某参考结构的节点子集" in section
