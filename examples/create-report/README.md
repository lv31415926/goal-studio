# 示例: 用 goal-studio 生成项目周报

完整演示 DocAdapter 全生命周期: draft → refine → validate → build → export

## 运行

```bash
cd goal-studio  # 项目根目录
python3 examples/create-report/run.py
```

## 流程说明

1. **定义目标契约** — 项目周报 (进度/风险/下周计划 + 数据图表)
2. **起草 DAG** — DocAdapter.draft() 生成三层 DAG (13 节点)
3. **精化节点** — 给数据采集节点添加数据源 (git log / jira API / ci logs)
4. **验证** — 5 维评估 (structure / completeness / research_depth / review_rigor / formatting)
5. **构建** — 生成 document.md 报告文件
6. **导出** — 持久化到 output/ 目录

## 预期输出

```
examples/create-report/output/
  └── doc-<hash>/
      └── document.md    # 生成的周报
```

## 也可通过 CLI 运行

```bash
goal-studio init --name weekly-report && cd weekly-report
goal-studio draft \
  --goal "生成项目周报,涵盖进度/风险/下周计划" \
  --type doc \
  --criteria "包含数据图表"
goal-studio view
goal-studio refine l1-research --patch "配置数据源: git log, jira API"
goal-studio verify
goal-studio build -o ./output
```
