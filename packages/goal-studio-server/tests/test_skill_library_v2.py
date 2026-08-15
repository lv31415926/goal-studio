"""Voyager 深化: build 后产物骨架补全 + CLI skills 可见性"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from test_p0_upgrade import SpyAdapter, _draft, _mk_session  # noqa: F401 (复用)

from goal_studio_server.app import create_app
from goal_studio_server.deps import (
    get_adapter_registry,
    get_knowledge_store,
    get_llm_provider,
    get_session_manager,
)
from goal_studio_server.routes.studio import _extract_outline

# ==== 骨架提取单元 ====

def test_extract_outline_markdown_and_python() -> None:
    doc_line = chr(34) * 3 + "doc" + chr(34) * 3
    py_lines = [
        doc_line, "",
        "def main() -> None:",
        "    print(\"hi\")", "", "",
        "class Foo:", "    pass",
    ]
    files = {
        "SKILL.md": "# 技能\n正文\n## §1 命令\n内容\n## §2 工作流\n",
        "src/main.py": "\n".join(py_lines),
        "README.md": "# R\n正文",
    }
    outline = _extract_outline(files)
    assert outline["SKILL.md"] == ["# 技能", "## §1 命令", "## §2 工作流"]
    assert "def main() -> None:" in outline["src/main.py"]
    assert "class Foo:" in outline["src/main.py"]
    assert "正文" not in " ".join(outline["SKILL.md"])


def test_extract_outline_limits() -> None:
    """总行数封顶 60,超限截断不爆"""
    files = {f"f{i}.md": "\n".join(f"# H{j}" for j in range(30)) for i in range(5)}
    outline = _extract_outline(files)
    total = sum(len(v) for v in outline.values())
    assert total <= 60
    # 每文件 ≤20
    assert all(len(v) <= 20 for v in outline.values())


def test_extract_outline_empty_content() -> None:
    assert _extract_outline({"a.md": "无标题文档\n只有正文"}) == {}
    assert _extract_outline({}) == {}


# ==== API 闭环: build → pattern 补全骨架 ====

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOAL_STUDIO_SESSION_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setenv("GOAL_STUDIO_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GOAL_STUDIO_KNOWLEDGE_ROOT", str(tmp_path / "knowledge.json"))
    import goal_studio_server.deps as deps

    for fn in (deps.get_session_manager, deps.get_artifact_store, deps.get_knowledge_store):
        fn.cache_clear()
    yield tmp_path
    for fn in (deps.get_session_manager, deps.get_artifact_store, deps.get_knowledge_store):
        fn.cache_clear()


@pytest.fixture
def client(env):
    from goal_studio.core.session_manager import SessionManager

    spy = SpyAdapter()

    class _Reg:
        def get_for_goal(self, goal):
            return spy

        def get(self, t):
            return spy

    sm = SessionManager(root=env / "sessions")
    app = create_app()
    app.dependency_overrides[get_session_manager] = lambda: sm
    app.dependency_overrides[get_adapter_registry] = lambda: _Reg()
    app.dependency_overrides[get_llm_provider] = lambda: None
    with TestClient(app) as c:
        yield c, spy


def test_build_enriches_pattern_outline(client):
    """validate PASS 沉淀 → build 后 pattern 含 artifact_outline,置信度+0.02"""
    c, spy = client
    sid = _mk_session(c, goal="创建量化技能")
    _draft(c, sid, goal="创建量化技能")
    spy.scores = [88.0]
    assert c.post(f"/api/sessions/{sid}/validate").json()["passed"] is True
    conf_before = get_knowledge_store().get(f"pattern:{sid}").confidence

    assert c.post(f"/api/sessions/{sid}/build").status_code == 200

    entry = get_knowledge_store().get(f"pattern:{sid}")
    pat = json.loads(entry.value)
    assert "artifact_outline" in pat
    assert "SKILL.md" in pat["artifact_outline"]
    assert entry.confidence == pytest.approx(min(0.95, conf_before + 0.02))


def test_build_without_pattern_noop(client):
    """未沉淀 pattern(未 validate)的会话, build 补全静默跳过不报错"""
    c, spy = client
    sid = _mk_session(c, goal="直接构建")
    _draft(c, sid, goal="直接构建")
    # phase 校验需先 validate;此处只验证 enrich 不炸
    spy.scores = [88.0]
    c.post(f"/api/sessions/{sid}/validate")
    # 删掉 pattern 再 build — enrich 应静默
    store = get_knowledge_store()
    if store.get(f"pattern:{sid}"):
        store.delete(f"pattern:{sid}")
    assert c.post(f"/api/sessions/{sid}/build").status_code == 200
    assert store.get(f"pattern:{sid}") is None


# ==== CLI skills ====

def test_cli_skills_lists_patterns(env, monkeypatch):
    """CLI skills 输出表格: 目标/分数/L1/骨架"""
    c, spy = client_gen = None, None  # noqa: F841 — 复用 env 写入数据即可
    from goal_studio.core.knowledge_store import KnowledgeEntry, LocalFileKnowledgeStore

    store = LocalFileKnowledgeStore(env / "knowledge.json")
    store.put(KnowledgeEntry(
        id="p1", category="pattern", key="pattern:s1",
        value=json.dumps({
            "goal": "创建量化交易技能", "l1_nodes": [{"title": "a"}, {"title": "b"}],
            "score": 88.5, "artifact_outline": {"SKILL.md": ["# s", "## §1"]},
        }),
        source="auto", confidence=0.9,
    ))
    from goal_studio.cli.main import cli

    runner = CliRunner()
    r = runner.invoke(cli, ["skills", "--knowledge-root", str(env / "knowledge.json")])
    assert r.exit_code == 0, r.output
    assert "创建量化交易技能" in r.output
    assert "88.5" in r.output
    assert "1 文件" in r.output


def test_cli_skills_empty_and_missing(tmp_path):
    from goal_studio.cli.main import cli

    runner = CliRunner()
    r = runner.invoke(cli, ["skills", "--knowledge-root", str(tmp_path / "nope.json")])
    assert r.exit_code == 0
    assert "技能库不存在" in r.output
