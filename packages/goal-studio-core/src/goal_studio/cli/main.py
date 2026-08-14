"""goal-studio CLI 主命令

命令:
  goal-studio init          初始化项目
  goal-studio draft         起草三层 DAG
  goal-studio view          可视化呈现(启动 Web)
  goal-studio refine        精化指定节点
  goal-studio verify        三重门控验证
  goal-studio build         固化产物
  goal-studio serve         启动 Web 服务(等同 view 长驻)
  goal-studio adapters      列出已注册适配器
  goal-studio sessions      列出所有会话
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from goal_studio.core.adapter_registry import get_default_registry
from goal_studio.core.contract import GoalContract, ProductType
from goal_studio.core.session_manager import SessionManager

console = Console()


@click.group()
@click.version_option()
def cli() -> None:
    """goal-studio - 可视化 Loop 创作系统"""


@cli.command()
@click.option("--name", default="my-project", help="项目名称")
@click.option("--dir", "dir_", default=".", help="项目目录")
def init(name: str, dir_: str) -> None:
    """初始化 goal-studio 项目"""
    project_dir = Path(dir_) / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "sessions").mkdir(exist_ok=True)
    (project_dir / "artifacts").mkdir(exist_ok=True)
    (project_dir / "goal.yaml").write_text(
        f"""# goal-studio 项目配置
name: {name}
created: auto
""",
        encoding="utf-8",
    )
    console.print(f"[green]✓ 项目已初始化: {project_dir}[/green]")


@cli.command()
@click.option("--goal", required=True, help="目标描述")
@click.option(
    "--type",
    "product_type",
    type=click.Choice([p.value for p in ProductType]),
    default="skill",
    help="产物类型",
)
@click.option("--constraint", multiple=True, help="约束条件(可多次)")
@click.option("--criteria", multiple=True, help="成功标准(可多次)")
@click.option("--session-dir", default="./sessions", help="会话目录")
def draft(
    goal: str,
    product_type: str,
    constraint: tuple[str, ...],
    criteria: tuple[str, ...],
    session_dir: str,
) -> None:
    """起草三层 DAG"""
    registry = get_default_registry()
    try:
        adapter = registry.get(product_type)
    except KeyError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)

    goal_contract = GoalContract(
        goal=goal,
        constraints=list(constraint),
        success_criteria=list(criteria),
        product_type=ProductType(product_type),
    )

    issues = goal_contract.validate()
    for issue in issues:
        console.print(f"[yellow]⚠ {issue}[/yellow]")

    graph = adapter.draft(goal_contract)
    console.print(
        f"[green]✓ DAG 已起草: {len(graph.nodes)} 个节点, {len(graph.edges)} 条边[/green]"
    )

    # 保存会话
    sm = SessionManager(session_dir)
    session = sm.create(name=f"draft-{product_type}", goal=goal_contract)
    session.update_graph(graph)
    sm.save(session)
    console.print(f"[green]✓ 会话已保存: {session.id}[/green]")
    console.print(f"  节点: {list(graph.nodes.keys())}")


@cli.command()
@click.option("--session-dir", default="./sessions", help="会话目录")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def view(session_dir: str, fmt: str) -> None:
    """可视化呈现当前 DAG"""
    sm = SessionManager(session_dir)
    sessions = sm.list_sessions()
    if not sessions:
        console.print("[yellow]⚠ 没有会话,请先 /studio draft[/yellow]")
        return

    latest = sessions[0]
    session = sm.get(latest["id"])
    if not session.graph:
        console.print("[yellow]⚠ 会话图为空[/yellow]")
        return

    if fmt == "json":
        click.echo(json.dumps(session.graph.to_dict(), ensure_ascii=False, indent=2))
    else:
        table = Table(title=f"DAG (session={session.id}, v{session.graph.version})")
        table.add_column("Layer", style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Title")
        table.add_column("Status")
        table.add_column("Description")

        from goal_studio.core.node_graph import NodeLayer

        for layer in [NodeLayer.L1_PLANNING, NodeLayer.L2_ALLOCATION, NodeLayer.L3_REFINEMENT]:
            for node in session.graph.get_layer(layer):
                table.add_row(
                    f"L{layer.value}",
                    node.id,
                    node.title,
                    node.status.value,
                    node.description[:50],
                )
        console.print(table)


@cli.command()
@click.argument("node_id")
@click.option("--patch", required=True, help="精化补丁(JSON 或自然语言)")
@click.option("--session-dir", default="./sessions")
def refine(node_id: str, patch: str, session_dir: str) -> None:
    """精化指定节点"""
    from goal_studio.core.contract import Patch, PatchOp

    sm = SessionManager(session_dir)
    sessions = sm.list_sessions()
    if not sessions:
        console.print("[yellow]⚠ 没有会话[/yellow]")
        return

    session = sm.get(sessions[0]["id"])
    if not session.graph:
        console.print("[yellow]⚠ 会话图为空[/yellow]")
        return

    # MVP: 把 patch 字符串当作 description 更新
    # Phase 2: 走 IntentParser LLM 解析
    patch_obj = Patch(
        op=PatchOp.UPDATE_NODE,
        target_node_id=node_id,
        slots={"description": patch},
        source="text",
        rationale="CLI refine",
    )

    # 取对应适配器
    if not session.goal:
        console.print("[red]✗ 会话缺少 goal[/red]")
        return
    registry = get_default_registry()
    adapter = registry.get_for_goal(session.goal)
    new_graph = adapter.refine(session.graph, patch_obj)
    session.update_graph(new_graph)
    sm.save(session)
    console.print(f"[green]✓ 节点 {node_id} 已精化 (graph v{new_graph.version})[/green]")


@cli.command()
@click.option("--session-dir", default="./sessions")
def verify(session_dir: str) -> None:
    """三重门控验证"""
    from goal_studio.core.ratchet_gate import RatchetDecision, RatchetGate

    sm = SessionManager(session_dir)
    sessions = sm.list_sessions()
    if not sessions:
        console.print("[yellow]⚠ 没有会话[/yellow]")
        return

    session = sm.get(sessions[0]["id"])
    if not session.graph or not session.goal:
        console.print("[yellow]⚠ 会话不完整[/yellow]")
        return

    registry = get_default_registry()
    adapter = registry.get_for_goal(session.goal)

    # MVP: 单评估器(Phase 2 接独立评审)
    report = adapter.validate(session.graph)
    console.print(f"[cyan]评估者: {report.evaluator}[/cyan]")
    color = "green" if report.passed else "red"
    status = "PASS" if report.passed else "FAIL"
    console.print(f"[{color}]分数: {report.score:.1f} ({status})[/]")
    for dim, score in report.dimensions.items():
        console.print(f"  {dim}: {score:.1f}")
    if report.issues:
        console.print("[yellow]问题:[/yellow]")
        for issue in report.issues:
            console.print(f"  - {issue}")
    if report.suggestions:
        console.print("[blue]建议:[/blue]")
        for s in report.suggestions:
            console.print(f"  - {s}")

    # 棘轮判定
    gate = RatchetGate()
    decision = gate.judge(session.best_score, report.score, session.graph)
    session.ratchet_history.append({
        "round": len(session.ratchet_history) + 1,
        "old_score": session.best_score,
        "new_score": report.score,
        "decision": decision.value,
    })
    if decision == RatchetDecision.KEEP:
        session.best_score = report.score
        session.best_graph = session.graph
        console.print("[green]✓ 棘轮判定: KEEP (分数提升)[/green]")
    else:
        console.print(f"[yellow]↺ 棘轮判定: {decision.value}[/yellow]")
    sm.save(session)


@cli.command()
@click.option("--session-dir", default="./sessions")
@click.option("--output", "-o", default="./artifacts", help="输出目录")
@click.option("--target", default="file://./artifacts", help="导出目标")
def build(session_dir: str, output: str, target: str) -> None:
    """固化产物"""
    from goal_studio.core.artifact_store import LocalFileArtifactStore

    sm = SessionManager(session_dir)
    sessions = sm.list_sessions()
    if not sessions:
        console.print("[yellow]⚠ 没有会话[/yellow]")
        return

    session = sm.get(sessions[0]["id"])
    if not session.graph or not session.goal:
        console.print("[yellow]⚠ 会话不完整[/yellow]")
        return

    registry = get_default_registry()
    adapter = registry.get_for_goal(session.goal)
    artifact = adapter.build(session.graph)

    # 持久化
    store = LocalFileArtifactStore(output)
    artifact_id = store.save(artifact, note=f"session={session.id}")
    session.best_artifact_id = artifact_id
    sm.save(session)
    console.print(f"[green]✓ 产物已保存: {artifact_id}[/green]")

    # 导出
    location = adapter.export(artifact, target)
    console.print(f"[green]✓ 已导出: {location}[/green]")


@cli.command()
@click.option("--port", default=5173, help="端口")
def serve(port: int) -> None:
    """启动 Web 服务"""
    console.print(f"[cyan]启动 goal-studio Web 服务 (port={port})...[/cyan]")
    console.print("[yellow]Phase 2 实现: 启动 React 前端 + FastAPI 后端[/yellow]")


@cli.command()
def adapters() -> None:
    """列出已注册适配器"""
    registry = get_default_registry()
    table = Table(title="已注册适配器")
    table.add_column("product_type", style="cyan")
    table.add_column("name")
    table.add_column("description")
    for a in registry.list_adapters():
        table.add_row(a["product_type"], a["name"], a["description"])
    console.print(table)


@cli.command()
@click.option("--name", required=True, help="适配器名称(如 MyAdapter)")
@click.option("--type", "product_type", required=True, help="产物类型标识(如 custom)")
@click.option("--desc", default="", help="一句话描述")
@click.option("--output", "-o", default="./adapters", help="输出目录")
def scaffold(name: str, product_type: str, desc: str, output: str) -> None:
    """生成适配器脚手架代码 (Plugin SDK)"""
    from goal_studio.core.base_adapter import BaseAdapter
    from goal_studio.core.plugin_sdk import scaffold as _scaffold
    from goal_studio.core.plugin_sdk import validate_adapter

    file_path = _scaffold(name, product_type, output, desc)
    console.print(f"[green]✓ 适配器脚手架已生成: {file_path}[/green]")

    # 尝试加载并验证
    try:
        import importlib.util
        mod_spec = importlib.util.spec_from_file_location("_scaffold_test", file_path)
        if mod_spec is None or mod_spec.loader is None:
            console.print("[yellow]⚠ 无法加载模块[/yellow]")
            return
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (isinstance(attr, type) and issubclass(attr, BaseAdapter)
                    and attr is not BaseAdapter and not attr.__name__.startswith("_")):
                issues = validate_adapter(attr())
                if issues:
                    console.print("[yellow]⚠ 验证问题:[/yellow]")
                    for issue in issues:
                        console.print(f"  - {issue}")
                else:
                    console.print("[green]✓ 适配器验证通过[/green]")
                break
    except Exception as e:
        console.print(f"[yellow]⚠ 无法自动验证: {e}[/yellow]")

    console.print("\n[cyan]下一步:[/cyan]")
    console.print(f"  1. 编辑 {file_path} 实现具体逻辑")
    console.print("  2. 将文件放入适配器目录(如 ./adapters/)")
    console.print("  3. 运行 goal-studio adapters 查看是否加载成功")


@cli.command()
@click.option("--dir", "dir_", default="./adapters", help="适配器目录")
def load_plugins(dir_: str) -> None:
    """从目录热加载适配器 (Plugin SDK)"""
    registry = get_default_registry()
    loaded = registry.load_from_directory(dir_)
    if loaded:
        console.print(f"[green]✓ 加载了 {len(loaded)} 个适配器: {loaded}[/green]")
    else:
        console.print(f"[yellow]⚠ 未找到适配器(目录: {dir_})[/yellow]")


@cli.command()
@click.option("--session", "-s", required=True, help="会话 ID")
@click.option("--target", default="maxfun", help="安装目标 (maxfun/file)")
@click.option("--skill-dir", default="", help="maxfun skill 目录(默认自动探测)")
@click.option("--name", default="", help="skill 名称(默认从产物提取)")
def install(session: str, target: str, skill_dir: str, name: str) -> None:
    """安装产物到运行环境 (maxfun-adapter)"""
    from goal_studio.core.artifact_store import LocalFileArtifactStore
    from goal_studio.core.session_manager import SessionManager

    sm = SessionManager("./sessions")
    try:
        s = sm.get(session)
    except KeyError:
        console.print(f"[red]✗ 会话不存在: {session}[/red]")
        return
    if not s.best_artifact_id:
        console.print("[red]✗ 会话无构建产物,请先运行 build[/red]")
        return

    store = LocalFileArtifactStore("./artifacts")
    artifact = store.load(s.best_artifact_id)
    if not artifact:
        console.print(f"[red]✗ 产物不存在: {s.best_artifact_id}[/red]")
        return

    skill_name = name or artifact.metadata.get("skill_name", session)

    if target == "maxfun":
        # 动态加载 maxfun-adapter
        import sys
        plugin_path = (
            Path(__file__).resolve().parents[5] / "plugins" / "maxfun-adapter"
        )
        if str(plugin_path) not in sys.path:
            sys.path.insert(0, str(plugin_path))
        try:
            from maxfun_bridge import (  # type: ignore[import-not-found]
                detect_maxfun_dir,
                install_to_maxfun,
            )
        except ImportError:
            console.print("[red]✗ maxfun-adapter 未安装[/red]")
            console.print("[cyan]请将 plugins/maxfun-adapter/ 加入 PYTHONPATH[/cyan]")
            return

        mf_dir = skill_dir or detect_maxfun_dir()
        if not mf_dir:
            console.print("[yellow]⚠ 未找到 maxfun skill 目录,使用 ~/.maxfun/skills/[/yellow]")
            mf_dir = str(Path.home() / ".maxfun" / "skills")
            Path(mf_dir).mkdir(parents=True, exist_ok=True)

        installed_path = install_to_maxfun(artifact.files, skill_name, mf_dir)
        console.print(f"[green]✓ 已安装到 maxfun: {installed_path}[/green]")
        console.print(f"  skill: {skill_name}")
        console.print(f"  文件: {len(artifact.files)} 个")
        console.print(f"  目录: {mf_dir}")
    else:
        # 默认 file 安装(使用 adapter.install)
        if s.goal is None:
            console.print("[red]✗ 会话无目标契约,无法安装[/red]")
            return
        adapter = get_default_registry().get_for_goal(s.goal)
        result = adapter.install(artifact, {"skill_dir": skill_dir or "./output"})
        console.print(f"[green]✓ {result}[/green]")


@cli.command()
@click.option("--from", "from_target", default="maxfun", help="导入来源 (maxfun)")
@click.option("--skill-dir", required=True, help="maxfun skill 目录路径")
@click.option("--name", default="", help="skill 名称(默认从目录提取)")
def import_skill(from_target: str, skill_dir: str, name: str) -> None:
    """从 maxfun 导入 skill 为 goal-studio DAG (maxfun-adapter)"""
    import sys
    plugin_path = (
            Path(__file__).resolve().parents[5] / "plugins" / "maxfun-adapter"
        )
    if str(plugin_path) not in sys.path:
        sys.path.insert(0, str(plugin_path))
    try:
        from maxfun_bridge import import_from_maxfun
    except ImportError:
        console.print("[red]✗ maxfun-adapter 未安装[/red]")
        return

    result = import_from_maxfun(skill_dir)
    meta = result["skill_meta"]
    dag_hint = result["dag_hint"]

    console.print(f"[green]✓ 导入成功: {meta['name']}[/green]")
    console.print(f"  描述: {meta['description']}")
    console.print(f"  版本: {meta['version']}")
    console.print(f"  触发词: {', '.join(meta['triggers']) or '(无)'}")
    console.print(f"  文件数: {len(result['files'])}")
    console.print(f"  DAG 提示: {len(dag_hint)} 个 L1 节点")

    if dag_hint:
        console.print("\n[cyan]建议的 DAG 结构:[/cyan]")
        for node in dag_hint:
            console.print(f"  {node['id']} [{node['type']}] {node['title']}")


@cli.command()
@click.option("--dir", "dir_", default="", help="maxfun skill 目录(默认自动探测)")
def list_skills(dir_: str) -> None:
    """列出 maxfun 目录中的 skill (maxfun-adapter)"""
    import sys
    plugin_path = (
            Path(__file__).resolve().parents[5] / "plugins" / "maxfun-adapter"
        )
    if str(plugin_path) not in sys.path:
        sys.path.insert(0, str(plugin_path))
    try:
        from maxfun_bridge import detect_maxfun_dir, list_maxfun_skills
    except ImportError:
        console.print("[red]✗ maxfun-adapter 未安装[/red]")
        return

    mf_dir = dir_ or detect_maxfun_dir()
    if not mf_dir:
        console.print("[yellow]⚠ 未找到 maxfun skill 目录[/yellow]")
        console.print("[cyan]设置 MAXFUN_SKILL_DIR 或创建 ~/.maxfun/skills/[/cyan]")
        return

    skills = list_maxfun_skills(mf_dir)
    if not skills:
        console.print(f"[yellow]⚠ 目录中无 skill: {mf_dir}[/yellow]")
        return

    table = Table(title=f"maxfun skills ({mf_dir})")
    table.add_column("名称", style="cyan")
    table.add_column("版本")
    table.add_column("描述", style="dim")
    table.add_column("触发词")
    table.add_column("文件数", justify="right")
    for sk in skills:
        table.add_row(
            sk["name"], sk["version"], sk["description"][:40],
            ", ".join(sk["triggers"]) or "-",
            str(sk["file_count"]),
        )
    console.print(table)


@cli.command()
@click.option("--session-dir", default="./sessions")
def sessions(session_dir: str) -> None:
    """列出所有会话"""
    sm = SessionManager(session_dir)
    table = Table(title="会话列表")
    table.add_column("ID", style="dim")
    table.add_column("名称")
    table.add_column("阶段")
    table.add_column("最佳分数")
    table.add_column("更新时间")
    for s in sm.list_sessions():
        table.add_row(s["id"], s["name"], s["phase"], f"{s['best_score']:.1f}", s["updated_at"])
    console.print(table)


if __name__ == "__main__":
    cli()
