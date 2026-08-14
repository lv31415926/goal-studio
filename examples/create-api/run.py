#!/usr/bin/env python3
"""端到端示例: 用 goal-studio 开发一个用户登录 API

完整演示 CodeAdapter 全生命周期:
  draft → view → refine → validate → build → export

运行: python3 examples/create-api/run.py
"""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from goal_studio.core.adapter_registry import get_default_registry
from goal_studio.core.contract import GoalContract, Patch, PatchOp, ProductType
from goal_studio.core.artifact_store import LocalFileArtifactStore
from goal_studio.core.node_graph import NodeLayer

console = Console()
OUTPUT_DIR = Path(__file__).parent / "output"


def main() -> None:
    console.print("\n[bold cyan]═══ goal-studio 端到端示例: 开发用户登录 API ═══[/bold cyan]\n")

    # ── 1. 定义目标契约 ──
    goal = GoalContract(
        goal="开发一个用户登录 API,支持邮箱和手机号登录,JWT 令牌认证",
        prerequisites=["Python 3.11+", "FastAPI 已安装"],
        constraints=["使用 FastAPI 框架", "密码用 bcrypt 加密", "JWT 过期时间 24h"],
        success_criteria=["POST /login 返回 JWT", "错误密码返回 401", "通过 pytest 测试"],
        product_type=ProductType.CODE,
    )
    console.print("[bold]1. 目标契约[/bold]")
    console.print(f"   goal: {goal.goal}")
    console.print(f"   constraints: {len(goal.constraints)} 项")
    console.print(f"   criteria: {len(goal.success_criteria)} 项\n")

    # ── 2. 起草 DAG ──
    registry = get_default_registry()
    adapter = registry.get("code")
    console.print("[bold]2. 起草三层 DAG[/bold] (CodeAdapter.draft)")
    graph = adapter.draft(goal)
    console.print(f"   [green]✓[/green] {len(graph.nodes)} 节点, {len(graph.edges)} 边, v{graph.version}")

    table = Table(title="API 开发 DAG")
    table.add_column("Layer", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Description", style="dim")
    for layer in [NodeLayer.L1_PLANNING, NodeLayer.L2_ALLOCATION, NodeLayer.L3_REFINEMENT]:
        for node in graph.get_layer(layer):
            table.add_row(
                f"L{layer.value}", node.id, node.title, node.node_type,
                node.description[:40],
            )
    console.print(table)

    # ── 3. 精化: 给数据模型节点加字段定义 ──
    console.print("\n[bold]3. 精化节点[/bold] (CodeAdapter.refine)")
    l1_nodes = graph.get_layer(NodeLayer.L1_PLANNING)
    target_node = l1_nodes[0] if l1_nodes else None
    if target_node:
        patch = Patch(
            op=PatchOp.UPDATE_NODE,
            target_node_id=target_node.id,
            slots={
                "fields": ["email:str", "phone:str", "password_hash:str", "jwt_token:str"],
                "orm": "SQLAlchemy",
                "migration": "alembic",
            },
            source="example",
            rationale="定义用户模型字段和 ORM",
        )
        graph = adapter.refine(graph, patch)
        console.print(f"   [green]✓[/green] 已精化 {target_node.id} (v{graph.version})")

    # ── 4. 验证 ──
    console.print("\n[bold]4. 验证[/bold] (CodeAdapter.validate)")
    report = adapter.validate(graph)
    status = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
    console.print(f"   评估者: {report.evaluator}")
    console.print(f"   分数: {report.score:.1f} {status}")
    for dim, score in report.dimensions.items():
        bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
        console.print(f"   {dim:30s} {bar} {score:.0f}")
    if report.suggestions:
        console.print("   [blue]建议:[/blue]")
        for s in report.suggestions:
            console.print(f"     - {s}")

    # ── 5. 构建 ──
    console.print("\n[bold]5. 构建产物[/bold] (CodeAdapter.build)")
    artifact = adapter.build(graph)
    console.print(f"   [green]✓[/green] 产物类型: {artifact.product_type}")
    console.print(f"   文件数: {len(artifact.files)}")
    for fname in artifact.files:
        size = len(artifact.files[fname])
        console.print(f"   - {fname} ({size} bytes)")

    # ── 6. 导出 ──
    console.print("\n[bold]6. 导出产物[/bold]")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    store = LocalFileArtifactStore(str(OUTPUT_DIR))
    artifact_id = store.save(artifact, note="create-api 示例")
    console.print(f"   [green]✓[/green] 已保存: {artifact_id}")

    # 打印主文件预览
    main_file = artifact.files.get("main.py", "") or artifact.files.get("app.py", "")
    if main_file:
        console.print("\n[bold]主文件预览 (前 25 行):[/bold]")
        for line in main_file.split("\n")[:25]:
            console.print(f"   {line}")

    console.print(f"\n[bold green]═══ 示例完成! 产物已保存到 {OUTPUT_DIR} ═══[/bold green]\n")


if __name__ == "__main__":
    main()
