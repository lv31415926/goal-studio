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

    # 旧格式 file://./rel (相对路径), 应被转为绝对路径 URI 而非根目录
    r = runner.invoke(
        cli,
        ["build", "--session-dir", sd, "--output", "./_legacy_out", "--target", "file://./_legacy_out"],
    )
    assert r.exit_code == 0, r.output
    assert "已导出" in r.output
    # 不应写到根目录 /_legacy_out
    assert not Path("/_legacy_out").exists()
