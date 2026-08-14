#!/usr/bin/env python3
"""端到端示例: 用 goal-studio 创建一个 A 股每日复盘技能

完整演示 SkillAdapter 全生命周期:
  draft → view → refine → validate → build → export

运行: python3 examples/create-skill/run.py
"""
from __future__ import annotations

import json
import sys
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
    console.print("\n[bold cyan]═══ goal-studio 端到端示例: 创建 A 股每日复盘技能 ═══[/bold cyan]\n")

    # ── 1. 定义目标契约 ──
    goal = GoalContract(
        goal="创建一个 A 股每日复盘技能,每个交易日收盘后自动汇总行情、资讯、资金流向并生成复盘报告",
        prerequisites=["已安装 maxfun-cli", "已有飞书/钉钉 Webhook"],
        constraints=["必须支持定时执行(每个交易日 15:30)", "复盘报告包含九板块", "输出 Markdown 长图"],
        success_criteria=["能正确获取当日涨停股数据", "能生成含图表的复盘报告", "能推送到飞书群"],
        product_type=ProductType.SKILL,
    )
    console.print("[bold]1. 目标契约[/bold]")
    console.print(f"   goal: {goal.goal[:60]}...")
    console.print(f"   constraints: {len(goal.constraints)} 项")
    console.print(f"   criteria: {len(goal.success_criteria)} 项\n")

    # ── 2. 起草 DAG ──
    registry = get_default_registry()
    adapter = registry.get("skill")
    console.print("[bold]2. 起草三层 DAG[/bold] (SkillAdapter.draft)")
    graph = adapter.draft(goal)
    console.print(f"   [green]✓[/green] {len(graph.nodes)} 节点, {len(graph.edges)} 边, v{graph.version}")

    table = Table(title="DAG 节点")
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

    # ── 3. 精化: 给行情采集节点加定时配置 ──
    console.print("\n[bold]3. 精化节点[/bold] (SkillAdapter.refine)")
    # 找到 L1 第一个节点(通常是需求分析或采集)
    l1_nodes = graph.get_layer(NodeLayer.L1_PLANNING)
    target_node = l1_nodes[0] if l1_nodes else None
    if target_node:
        patch = Patch(
            op=PatchOp.UPDATE_NODE,
            target_node_id=target_node.id,
            slots={
                "schedule": "cron: 30 15 * * 1-5",
                "retry_count": 3,
                "timeout": 300,
            },
            source="example",
            rationale="添加定时执行和重试配置",
        )
        graph = adapter.refine(graph, patch)
        console.print(f"   [green]✓[/green] 已精化 {target_node.id} (v{graph.version})")
        console.print(f"   slots: schedule=cron:30 15 * * 1-5, retry=3, timeout=300s")

    # ── 4. 验证 ──
    console.print("\n[bold]4. 验证[/bold] (SkillAdapter.validate)")
    report = adapter.validate(graph)
    status = "[green]PASS[/green]" if report.passed else "[red]FAIL[/red]"
    console.print(f"   评估者: {report.evaluator}")
    console.print(f"   分数: {report.score:.1f} {status}")
    for dim, score in report.dimensions.items():
        bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
        console.print(f"   {dim:30s} {bar} {score:.0f}")
    if report.issues:
        console.print("   [yellow]问题:[/yellow]")
        for issue in report.issues:
            console.print(f"     - {issue}")
    if report.suggestions:
        console.print("   [blue]建议:[/blue]")
        for s in report.suggestions:
            console.print(f"     - {s}")

    # ── 5. 构建 ──
    console.print("\n[bold]5. 构建产物[/bold] (SkillAdapter.build)")
    artifact = adapter.build(graph)
    console.print(f"   [green]✓[/green] 产物类型: {artifact.product_type}")
    console.print(f"   文件数: {len(artifact.files)}")
    for fname in artifact.files:
        size = len(artifact.files[fname])
        console.print(f"   - {fname} ({size} bytes)")

    # ── 6. 导出 ──
    console.print("\n[bold]6. 导出产物[/bold] (LocalFileArtifactStore)")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    store = LocalFileArtifactStore(str(OUTPUT_DIR))
    artifact_id = store.save(artifact, note="create-skill 示例")
    console.print(f"   [green]✓[/green] 已保存: {artifact_id}")
    console.print(f"   输出目录: {OUTPUT_DIR}")

    # 打印 SKILL.md 预览
    skill_content = artifact.files.get("SKILL.md", "")
    if skill_content:
        console.print("\n[bold]SKILL.md 预览 (前 30 行):[/bold]")
        for line in skill_content.split("\n")[:30]:
            console.print(f"   {line}")

    console.print(f"\n[bold green]═══ 示例完成! 产物已保存到 {OUTPUT_DIR} ═══[/bold green]\n")


if __name__ == "__main__":
    main()
