# 示例: 用 goal-studio 创建 A 股每日复盘技能

完整演示 SkillAdapter 全生命周期: draft → refine → validate → build → export

## 运行

```bash
cd goal-studio  # 项目根目录
python3 examples/create-skill/run.py
```

## 流程说明

1. **定义目标契约** — goal + prerequisites + constraints + success_criteria
2. **起草 DAG** — SkillAdapter.draft() 生成三层 DAG (13 节点)
3. **精化节点** — 给触发节点添加定时执行 (cron 15:30) 和重试配置
4. **验证** — 9 维评估 (workflow_clarity / executability / completeness ...)
5. **构建** — 生成 SKILL.md 技能文件
6. **导出** — 持久化到 output/ 目录

## 预期输出

```
examples/create-skill/output/
  └── skill-<hash>/
      └── SKILL.md    # 生成的技能文件
```

## 也可通过 CLI 运行

```bash
goal-studio init --name my-skill-project && cd my-skill-project
goal-studio draft \
  --goal "创建一个A股每日复盘技能,每个交易日收盘后自动汇总行情" \
  --type skill \
  --constraint "必须支持定时执行" \
  --criteria "能生成复盘报告"
goal-studio view
goal-studio refine l1-trigger --patch "增加定时执行 cron: 30 15 * * 1-5"
goal-studio verify
goal-studio build -o ./output
```
