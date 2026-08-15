"""回归测试: CLI phase 推进 + 适配器 export URI 解码

覆盖系统级缺陷修复:
1. CLI draft/refine/verify/build 必须推进 session.phase (与服务端 pipeline 一致)
2. CLI draft 会话名需含目标摘要, 避免多案例同名
3. 适配器 export() 必须对 file URI 做 unquote (空格/中文路径)
4. CLI build 兼容旧格式 --target file://./xxx
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from goal_studio.cli.main import cli


def _latest_session(session_dir: Path) -> dict:
    files = sorted(session_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    assert files, "未生成会话文件"
    return json.loads(files[-1].read_text(encoding="utf-8"))


def test_cli_phase_progression(tmp_path: Path, monkeypatch) -> None:
    """draft→verify→build 全流程 phase 应推进到 build"""
    # 确保无真实 LLM 环境变量, 走 fallback 模板 (快速且确定)
    for k in ("GOAL_STUDIO_LLM_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    sd = str(tmp_path / "sessions")
    out = str(tmp_path / "out")
    runner = CliRunner()

    r = runner.invoke(
        cli,
        ["draft", "--goal", "测试phase推进流程", "--type", "workflow", "--session-dir", sd],
    )
    assert r.exit_code == 0, r.output
    s = _latest_session(tmp_path / "sessions")
    assert s["phase"] == "draft", f"draft 后 phase 应为 draft, 实际 {s['phase']}"
    # 会话名需含目标摘要, 不能是通用 draft-workflow
    assert s["name"] != "draft-workflow"
    assert "测试phase推进流程" in s["name"]

    r = runner.invoke(cli, ["verify", "--session-dir", sd])
    assert r.exit_code == 0, r.output
    s = _latest_session(tmp_path / "sessions")
    assert s["phase"] == "verify", f"verify 后 phase 应为 verify, 实际 {s['phase']}"

    r = runner.invoke(cli, ["build", "--session-dir", sd, "--output", out])
    assert r.exit_code == 0, r.output
    s = _latest_session(tmp_path / "sessions")
    assert s["phase"] == "build", f"build 后 phase 应为 build, 实际 {s['phase']}"
    assert (Path(out) / "workflow.json").exists()


def test_cli_refine_phase(tmp_path: Path, monkeypatch) -> None:
    """refine 后 phase 应推进到 refine"""
    for k in ("GOAL_STUDIO_LLM_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    sd = str(tmp_path / "sessions")
    runner = CliRunner()
    r = runner.invoke(
        cli, ["draft", "--goal", "精化phase测试", "--type", "workflow", "--session-dir", sd]
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(
        cli, ["refine", "l1-trigger", "--patch", "补充触发器说明", "--session-dir", sd]
    )
    assert r.exit_code == 0, r.output
    s = _latest_session(tmp_path / "sessions")
    assert s["phase"] == "refine", f"refine 后 phase 应为 refine, 实际 {s['phase']}"


def test_export_unquote_space_and_chinese(tmp_path: Path) -> None:
    """export 必须正确解码含空格/中文的 file URI"""
    from goal_studio.adapters.workflow_adapter import WorkflowAdapter
    from goal_studio.core.contract import Artifact, ProductType

    out_dir = tmp_path / "out 目录"  # 含空格
    adapter = WorkflowAdapter()
    artifact = Artifact(
        product_type=ProductType.WORKFLOW,
        content="{}",
        files={"workflow.json": "{}"},
    )
    location = adapter.export(artifact, out_dir.as_uri())
    # 解码后应落在真实目录, 而非 %20 字面量目录
    assert Path(location) == out_dir
    assert (out_dir / "workflow.json").exists()
    assert not list(tmp_path.glob("*%20*")), "不应生成 %20 字面量目录"


def test_build_legacy_target_format(tmp_path: Path, monkeypatch) -> None:
    """build 显式传旧格式 --target file://./xxx 应被正确转换"""
    for k in ("GOAL_STUDIO_LLM_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    sd = str(tmp_path / "sessions")
    runner = CliRunner()
    r = runner.invoke(
        cli, ["draft", "--goal", "旧格式target测试", "--type", "workflow", "--session-dir", sd]
    )
    assert r.exit_code == 0, r.output

    # 在 tmp_path 下运行, 相对路径解析落在测试目录, 不污染仓库 cwd
    monkeypatch.chdir(tmp_path)
    # 旧格式 file://./rel (相对路径), 应被转为绝对路径 URI 而非根目录
    r = runner.invoke(
        cli,
        ["build", "--session-dir", sd, "--output", "./_legacy_out", "--target", "file://./_legacy_out"],
    )
    assert r.exit_code == 0, r.output
    assert "已导出" in r.output
    # 不应写到根目录 /_legacy_out
    assert not Path("/_legacy_out").exists()
    # 实际落在 cwd(tmp_path)下
    assert (tmp_path / "_legacy_out" / "workflow.json").exists()
def test_cli_verify_syncs_ratchet_best(tmp_path: Path, monkeypatch) -> None:
    """P0-1 回归: CLI verify 必须同步 session 与 ratchet 两侧的 best_score/best_graph"""
    for k in ("GOAL_STUDIO_LLM_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    sd = str(tmp_path / "sessions")
    runner = CliRunner()
    r = runner.invoke(
        cli, ["draft", "--goal", "棘轮同步测试", "--type", "workflow", "--session-dir", sd]
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(cli, ["verify", "--session-dir", sd])
    assert r.exit_code == 0, r.output
    assert "已同步最佳版本" in r.output

    from goal_studio.core.session_manager import SessionManager

    s_dict = _latest_session(tmp_path / "sessions")
    sm = SessionManager(sd)
    s = sm.get(s_dict["id"])
    # session 与 ratchet 两侧分数一致(修复前 ratchet.best_score 保持 -inf 不分叉)
    assert s.best_score > 0
    assert s.best_score == s.ratchet.best_score == s.current_score
    # 两侧 best_graph 均已同步且为深拷贝(修复前 ratchet.best_graph 为 None)
    assert s.best_graph is not None
    assert s.ratchet.best_graph is not None
    assert s.best_graph is not s.graph, "best_graph 必须是深拷贝,不能是浅引用"
    assert s.ratchet.best_graph.to_dict() == s.graph.to_dict()


def test_cli_verify_not_exceed_keeps_best(tmp_path: Path, monkeypatch) -> None:
    """P0-1: 第二次 verify 分数未超越时,保持已有 best_graph 不变"""
    for k in ("GOAL_STUDIO_LLM_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    sd = str(tmp_path / "sessions")
    runner = CliRunner()
    r = runner.invoke(
        cli, ["draft", "--goal", "二次verify测试", "--type", "workflow", "--session-dir", sd]
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(cli, ["verify", "--session-dir", sd])
    assert r.exit_code == 0, r.output

    from goal_studio.core.session_manager import SessionManager

    s_dict = _latest_session(tmp_path / "sessions")
    best_before = SessionManager(sd).get(s_dict["id"]).ratchet.best_graph.to_dict()

    # 同分再次 verify: 不超越历史最佳
    r = runner.invoke(cli, ["verify", "--session-dir", sd])
    assert r.exit_code == 0, r.output
    assert "未超越历史最佳" in r.output
    best_after = SessionManager(sd).get(s_dict["id"]).ratchet.best_graph.to_dict()
    assert best_after == best_before


def test_cli_verify_non_mutating_and_deep_copy(tmp_path: Path, monkeypatch) -> None:
    """P0-1: verify 不改变当前图; best_graph 深拷贝独立于后续图修改"""
    for k in ("GOAL_STUDIO_LLM_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    sd = str(tmp_path / "sessions")
    runner = CliRunner()
    r = runner.invoke(
        cli, ["draft", "--goal", "深拷贝验证", "--type", "workflow", "--session-dir", sd]
    )
    assert r.exit_code == 0, r.output

    from goal_studio.core.session_manager import SessionManager

    s_dict = _latest_session(tmp_path / "sessions")
    graph_before = json.dumps(s_dict["graph"], sort_keys=True)

    r = runner.invoke(cli, ["verify", "--session-dir", sd])
    assert r.exit_code == 0, r.output

    s = SessionManager(sd).get(s_dict["id"])
    assert json.dumps(s.graph.to_dict(), sort_keys=True) == graph_before, "verify 不得修改当前图"

    # 修改当前图节点标题, best_graph 不应受影响(浅引用会穿透)
    first_node = next(iter(s.graph.nodes.values()))
    best_node_title = s.ratchet.best_graph.nodes[first_node.id].title
    first_node.title = "已被后续修改"
    assert s.ratchet.best_graph.nodes[first_node.id].title == best_node_title
def test_cli_serve_invokes_uvicorn(monkeypatch) -> None:
    """C1: serve 命令真实启动 uvicorn(不再是占位 stub)"""
    pytest.importorskip("goal_studio_server", reason="server 包未安装")
    import sys
    import types

    calls: dict = {}
    fake_uvicorn = types.ModuleType("uvicorn")

    def _run(app_str: str, host: str, port: int) -> None:
        calls["app"] = app_str
        calls["host"] = host
        calls["port"] = port

    fake_uvicorn.run = _run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    r = CliRunner().invoke(cli, ["serve", "--port", "9000"])
    assert r.exit_code == 0, r.output
    assert calls == {
        "app": "goal_studio_server.app:app",
        "host": "127.0.0.1",
        "port": 9000,
    }


def test_cli_build_dry_run(tmp_path: Path, monkeypatch) -> None:
    """F2: build --dry-run 只打印文件清单,不落盘不导出、phase 不推进"""
    for k in ("GOAL_STUDIO_LLM_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    sd = str(tmp_path / "sessions")
    out = tmp_path / "dryout"
    runner = CliRunner()
    r = runner.invoke(
        cli, ["draft", "--goal", "dryrun测试", "--type", "workflow", "--session-dir", sd]
    )
    assert r.exit_code == 0, r.output

    r = runner.invoke(
        cli, ["build", "--session-dir", sd, "--output", str(out), "--dry-run"]
    )
    assert r.exit_code == 0, r.output
    assert "dry-run" in r.output
    assert "workflow.json" in r.output
    # 不落盘: 输出目录未创建
    assert not out.exists()
    # phase 不推进到 build
    s = _latest_session(tmp_path / "sessions")
    assert s["phase"] != "build"
