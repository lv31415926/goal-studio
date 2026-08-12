# 示例: 用 goal-studio 生成报告

```bash
goal-studio init --name weekly-report
cd weekly-report

goal-studio draft \
  --goal "生成项目周报,涵盖进度/风险/下周计划" \
  --type doc \
  --criteria "包含数据图表"
```

Phase 2 实现 DocAdapter 后可用。
